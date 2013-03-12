#!/usr/bin/env python

from __future__ import with_statement

from errno import EACCES, ENOENT, EIO, EPERM
from sys import argv, exit
from threading import Lock
from stat import S_IFDIR, S_IFREG
from sys import argv, exit

import os
import tempfile
import time
import json
import hashlib
import urllib3

from fuse import FUSE, FuseOSError, Operations, LoggingMixIn, fuse_get_context

class CopyAPI:
    headers = {'X-Client-Type': 'api', 'X-Api-Version': '0.1.18', "Content-type": "application/x-www-form-urlencoded", "Accept": "text/plain"}

    def __init__(self, username, password):
        self.auth_token = ''
        self.tree_children = {}
        self.tree_expire = {}
        self.httpconn = urllib3.connection_from_url("https://api.copy.com", block=True, maxsize=1)
        data = {'username': username, 'password' : password}
        response = self.copyrequest('/auth_user', data)
        if 'auth_token' not in response:
            raise FuseOSError(EPERM)
        else:
            self.auth_token = response['auth_token'].encode('ascii','ignore')

    def copyrequest(self, uri, data, return_json=True):
        headers = self.headers
        if self.auth_token != '':
            headers['X-Authorization'] = self.auth_token
        response = self.httpconn.request_encode_body("POST", uri, {'data': json.dumps(data)}, headers, False)

        if return_json == True:
            return json.loads(response.data, 'latin-1')
        else:
            return response.data

    def part_request(self, method, parts, data=None):
        headers = self.headers
        headers['X-Part-Count'] = len(parts)

        payload = ''

        for i in range(0, len(parts)):
            part_num = str(i + 1)
            headers['X-Part-Fingerprint-' + part_num] = parts[i]['fingerprint']
            headers['X-Part-Size-' + part_num] = parts[i]['size']
            headers['X-Part-Share-' + part_num] = 0

            if method == 'send_parts':
                payload = payload + parts[i]['data']

        # authentication http headers
        if self.auth_token != '':
            headers['X-Authorization'] = self.auth_token

        # print headers

        if method == 'has_parts':
            response = self.httpconn.request_encode_body("POST", "/" + method, {'data': json.dumps(data)}, headers, False)
        else:
            response = self.httpconn.urlopen("POST", "/" + method, payload, headers)

        return json.loads(response.data, 'latin-1')

    def list_objects(self, path, ttl=10):
        # check cache
        if path in self.tree_expire:
            if self.tree_expire[path] >= time.time():
                return self.tree_children[path]

        # obtain data from copy
        # print "listing objects from cloud for path: " + path
        data = {'path': path, 'max_items': 1000000}
        response = self.copyrequest('/list_objects', data)
        if 'children' not in response:
            raise FuseOSError(EIO)

        # build tree
        self.tree_children[path] = {}
        for child in response['children']:
            name = str(os.path.basename(child['path']))
            ctime = int(child['created_time'])
            if child['modified_time'] == None:
                mtime = ctime
            else:
                mtime = int(child['modified_time'])
            self.tree_children[path][name] = {'name': name, 'type': child['type'], 'size': child['size'], 'ctime': ctime, 'mtime': mtime}

        # update expiration time
        self.tree_expire[path] = time.time() + ttl

        return self.tree_children[path]

    def partify(self, f, size):
        parts = {}

        part_num = 0
        offset = 0
        while f.tell() < size:
            # obtain the part data
            offset = f.tell()
            part_data = f.read(1048576)
            parts[part_num] = {'fingerprint': hashlib.md5(part_data).hexdigest() + hashlib.sha1(part_data).hexdigest(), 'offset': offset, 'size': len(part_data), 'data': part_data}
            offset = f.tell()
            part_num += 1

        if size != offset:
            # print str(size) + " != " + str(offset)
            raise FuseOSError(EIO)

        return parts

class CopyFUSE(LoggingMixIn, Operations):
    def __init__(self, username, password):
        self.rwlock = Lock()
        self.copy_api = CopyAPI(username, password)
        self.files = {}

    def file_rename(self, old, new):
        if old in self.files:
            self.files[new] = self.files[old]
            del self.files[old]

    def file_get(self, path, download=True):
        if path in self.files:
            return self.files[path]

        if download == True:
            raw = self.copy_api.copyrequest("/download_object", {'path': path}, False)
        else:
            raw = ''

        f = tempfile.NamedTemporaryFile(delete=False)
        f.write(raw)
        self.files[path] = {'object': f, 'modified': False}

        # print "opening: " + path

        return self.files[path]

    def file_close(self, path):
        if path in self.files:
            if self.files[path]['modified'] == True:
                self.file_upload(path)

            # print "closing: " + path

            self.files[path]['object'].close()
            del self.files[path]

    def file_upload(self, path):
        if path not in self.files:
            raise FuseOSError(EIO)

        fileObject = self.file_get(path)
        if fileObject['modified'] == False:
            return True

        # print 'uploading: ' + path

        f = fileObject['object']

        # obtain the size of the file
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(0)

        parts = self.copy_api.partify(f, size)

        # obtain list of parts that need to be sent
        response = self.copy_api.part_request('has_parts', parts)

        if 'send_parts' not in response:
            raise FuseOSError(EIO)

        # build array of parts that need to be sent
        need_parts = {}
        for need_part in response['send_parts']:
            need_parts[need_part['fingerprint'] + '-' + need_part['size']] = True

        # send the missing parts
        send_parts = {}
        for i in range(0, len(parts)):
            if parts[i]['fingerprint'] + '-' + str(parts[i]['size']) in need_parts:
                send_parts[len(send_parts)] = parts[i]
        response = self.copy_api.part_request('send_parts', send_parts)

        # trap any errors
        if (response == False or response['result'] != 'success'):
            raise FuseOSError(EIO)

        # remove data from parts (already sent)
        for i in range(0, len(parts)):
            del parts[i]['data']

        # send file metadata
        params = {'meta': {}}
        params['meta'][0] = {'action': 'create', 'object_type': 'file', 'path': path, 'size': size, 'parts': parts}
        response = self.copy_api.copyrequest('/update_objects', params, True)

        # trap any errors
        if response['result'] != 'success':
            raise FuseOSError(EIO)

        fileObject['modified'] = False

    def chmod(self, path, mode):
        return 0

    def chown(self, path, uid, gid):
        return 0

    def statfs(self, path):
        return dict(f_bsize=512, f_blocks=4096, f_bavail=2048)

    def getattr(self, path, fh=None):
        # print "getattr: " + path
        if path == '/':
            st = dict(st_mode=(S_IFDIR | 0755), st_nlink=2)
            st['st_ctime'] = st['st_atime'] = st['st_mtime'] = time.time()
        else:
            name = str(os.path.basename(path))
            objects = self.copy_api.list_objects(os.path.dirname(path))

            if name not in objects:
                raise FuseOSError(ENOENT)
            elif objects[name]['type'] == 'file':
                st = dict(st_mode=(S_IFREG | 0644), st_size=int(objects[name]['size']))
            else:
                st = dict(st_mode=(S_IFDIR | 0755), st_nlink=2)

            st['st_ctime'] = st['st_atime'] = objects[name]['ctime']
            st['st_mtime'] = objects[name]['mtime']
        return st

    def mkdir(self, path, mode):
        # print "mkdir: " + path
        # send file metadata
        params = {'meta': {}}
        params['meta'][0] = {'action': 'create', 'object_type': 'dir', 'path': path}
        response = self.copy_api.copyrequest('/update_objects', params, True)

        # trap any errors
        if response['result'] != 'success':
            raise FuseOSError(EIO)

    def open(self, path, flags):
        # print "open: " + path
        self.file_get(path)
        return 0

    def flush(self, path, fh):
        # print "flush: " + path
        if path in self.files:
            if self.files[path]['modified'] == True:
                self.file_upload(path)

    def fsync(self, path, datasync, fh):
        # print "fsync: " + path
        if path in self.files:
            if self.files[path]['modified'] == True:
                self.file_upload(path)

    def release(self, path, fh):
        # print "release: " + path
        self.file_close(path)

    def read(self, path, size, offset, fh):
        f = self.file_get(path)['object']
        f.seek(offset)
        return f.read(size)

    def readdir(self, path, fh):
        # print "readdir: " + path
        objects = self.copy_api.list_objects(path)

        listing = ['.', '..']
        for child in objects:
            listing.append(child)
        return listing

    def rename(self, old, new):
        # print "renaming: " + old + " to " + new
        self.file_rename(old, new)
        params = {'meta': {}}
        params['meta'][0] = {'action': 'rename', 'path': old, 'new_path': new}
        self.copy_api.copyrequest("/update_objects", params, False)

    def create(self, path, mode):
        # print "create: " + path
        name = os.path.basename(path)
        if os.path.dirname(path) in self.copy_api.tree_children:
            self.copy_api.tree_children[os.path.dirname(path)][name] = {'name': name, 'type': 'file', 'size': 0, 'ctime': time.time(), 'mtime': time.time()}
        self.file_get(path, download=False)
        self.file_upload(path)
        return 0

    def truncate(self, path, length, fh=None):
        # print "truncate: " + path
        f = self.file_get(path)['object']
        f.truncate(length)

    def unlink(self, path):
        # print "unlink: " + path
        params = {'meta': {}}
        params['meta'][0] = {'action': 'remove', 'path': path}
        self.copy_api.copyrequest("/update_objects", params, False)

    def rmdir(self, path):
        params = {'meta': {}}
        params['meta'][0] = {'action': 'remove', 'path': path}
        self.copy_api.copyrequest("/update_objects", params, False)

    def write(self, path, data, offset, fh):
        fileObject = self.file_get(path)
        f = fileObject['object']
        f.seek(offset)
        f.write(data)
        fileObject['modified'] = True
        return len(data)

    # Disable unused operations:
    access = None
    getxattr = None
    listxattr = None
    opendir = None
    releasedir = None

if __name__ == "__main__":
    if len(argv) != 4:
        print 'usage: %s <username> <password> <mountpoint>' % argv[0]
        exit(1)
    fuse = FUSE(CopyFUSE(argv[1], argv[2]), argv[3], foreground=False)
