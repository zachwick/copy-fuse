#!/usr/bin/env python

from __future__ import with_statement

from errno import EACCES, ENOENT, EIO, EPERM
from sys import argv, exit
from threading import Lock
from stat import S_IFDIR, S_IFREG
from sys import argv, exit

import os
import time
import json
import httplib, urllib

from fuse import FUSE, FuseOSError, Operations, LoggingMixIn, fuse_get_context

class CopyAPI:
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
        headers = {'X-Client-Type': 'api', 'X-Api-Version': '0.1.18', "Content-type": "application/x-www-form-urlencoded", "Accept": "text/plain"}
        if self.auth_token != '':
            headers['X-Authorization'] = self.auth_token
        self.httpconn.request("POST", uri, urllib.urlencode({'data': json.dumps(data)}), headers)
        response = self.httpconn.getresponse()

        if return_json == True:
            return json.loads(response.read(), 'latin-1')
        else:
            return response.read()

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

class CopyFUSE(LoggingMixIn, Operations):
    def __init__(self, username, password):
        self.rwlock = Lock()
        self.copy_api = CopyAPI(username, password)

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
                st = dict(st_mode=(S_IFREG | 0444), st_size=int(objects[name]['size']))
            else:
                st = dict(st_mode=(S_IFDIR | 0555), st_nlink=2)

            st['st_ctime'] = st['st_atime'] = objects[name]['ctime']
            st['st_mtime'] = objects[name]['mtime']
        return st

    def read(self, path, size, offset, fh):
        raw = self.copy_api.copyrequest("/download_object", {'path': path}, False)
        return raw[offset:size]

    def readdir(self, path, fh):
        objects = self.copy_api.list_objects(path)

        listing = ['.', '..']
        for child in objects:
            listing.append(child)
        return listing

    # Disable unused operations:
    access = None
    flush = None
    getxattr = None
    listxattr = None
    open = None
    opendir = None
    release = None
    releasedir = None

if __name__ == "__main__":
    if len(argv) != 4:
        print 'usage: %s <username> <password> <mountpoint>' % argv[0]
        exit(1)
    fuse = FUSE(CopyFUSE(argv[1], argv[2]), argv[3], foreground=True)