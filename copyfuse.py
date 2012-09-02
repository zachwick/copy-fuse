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
import httplib, urllib

from fuse import FUSE, FuseOSError, Operations, LoggingMixIn, fuse_get_context

class CopyAPI:
    headers = {'X-Client-Type': 'api', 'X-Api-Version': '0.1.18', "Content-type": "application/x-www-form-urlencoded", "Accept": "text/plain"}

    def __init__(self, username, password):
        self.auth_token = ''
        self.tree_children = {}
        self.tree_expire = {}
        self.httpconn = httplib.HTTPSConnection("api.copy.com")
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
        self.httpconn.request("POST", uri, urllib.urlencode({'data': json.dumps(data)}), headers)
        response = self.httpconn.getresponse()

        if return_json == True:
            return json.loads(response.read(), 'latin-1')
        else:
            return response.read()

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
            self.httpconn.request("POST", "/" + method, urllib.urlencode({'data': json.dumps(data)}), headers)
        else:
            self.httpconn.request("POST", "/" + method, payload, headers)

        response = self.httpconn.getresponse()
        return json.loads(response.read(), 'latin-1')

    def list_objects(self, path, ttl=10):
        # check cache
        if path in self.tree_expire:
            if self.tree_expire[path] >= time.time():
                return self.tree_children[path]

        # obtain data from copy
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

    def partify(self, data):
        parts = {}

        if len(data) == 0:
            parts[0] = {'fingerprint': 'd41d8cd98f00b204e9800998ecf8427eda39a3ee5e6b4b0d3255bfef95601890afd80709', 'offset': 0, 'size': 0, 'data': ''}

        for byte_counter in xrange(0, len(data), 1048576):
            part_data = data[byte_counter:1048576]
            parts[len(parts)] = {'fingerprint': hashlib.md5(part_data).hexdigest() + hashlib.sha1(part_data).hexdigest(), 'offset': byte_counter, 'size': len(part_data), 'data': part_data}

        return parts

class CopyFUSE(LoggingMixIn, Operations):
    def __init__(self, username, password):
        self.rwlock = Lock()
        self.copy_api = CopyAPI(username, password)
        self.files = {}

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

        print "opening: " + path

        return self.files[path]

    def file_close(self, path):
        if path in self.files:
            if self.files[path]['modified'] == True:
                self.file_upload(path)

            print "closing: " + path

            self.files[path]['object'].close()
            del self.files[path]

    def file_upload(self, path):
        if path not in self.files:
            raise FuseOSError(EIO)

        fileObject = self.file_get(path)
        if fileObject['modified'] == False:
            return True

        print 'uploading: ' + path

        f = fileObject['object']
        f.seek(0)
        raw = f.read()
        parts = self.copy_api.partify(raw)

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
        params['meta'][0] = {'action': 'create', 'object_type': 'file', 'path': path, 'size': len(raw), 'parts': parts}
        response = self.copy_api.copyrequest('/update_objects', params, True)

        # trap any errors
        if response['result'] != 'success':
            raise FuseOSError(EIO)

        fileObject['modified'] = False

    def statfs(self, path):
        return dict(f_bsize=512, f_blocks=4096, f_bavail=2048)

    def getattr(self, path, fh=None):
        if path == '/':
            st = dict(st_mode=(S_IFDIR | 0555), st_nlink=2)
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

    def open(self, path, flags):
        self.file_get(path)
        return 0

    def flush(self, path, fh):
        self.file_upload(path)

    def fsync(self, path, datasync, fh):
        self.file_upload(path)

    def release(self, path, fh):
        self.file_upload(path)
        self.file_close(path)

    def read(self, path, size, offset, fh):
        f = self.file_get(path)['object']
        f.seek(offset)
        return f.read(size)

    def readdir(self, path, fh):
        objects = self.copy_api.list_objects(path)

        listing = ['.', '..']
        for child in objects:
            listing.append(child)
        return listing

    def create(self, path, mode):
        self.file_get(path, download=False)
        self.file_upload(path)
        return 0

    def truncate(self, path, length, fh=None):
        f = self.file_get(path)['object']
        f.truncate(length)

    def unlink(self, path):
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
    # release = None

if __name__ == "__main__":
    if len(argv) != 4:
        print 'usage: %s <username> <password> <mountpoint>' % argv[0]
        exit(1)
    fuse = FUSE(CopyFUSE(argv[1], argv[2]), argv[3], foreground=True)