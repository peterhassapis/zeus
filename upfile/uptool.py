#!/usr/bin/env python
# -*- coding: utf-8 -*-

from sys import argv
from httplib import HTTPConnection, HTTPSConnection
from urlparse import urlparse

def get_http_connection(url):
    parsed = urlparse(url)
    if parsed.scheme == 'https':
            default_port = '443'
            Conn = HTTPSConnection
    else:
            default_port = '80'
            Conn = HTTPConnection
    host, sep, port = parsed.netloc.partition(':')
    if not port:
        port = default_port
    netloc = host + ':' + port
    conn = Conn(netloc)
    conn.path = parsed.path
    return conn

def get_upfile_size(conn, raise_error=1):
    conn.request('GET', conn.path, headers={'Content-Range': 'bytes 0-0/*'})
    resp = conn.getresponse()
    body = resp.read()
    if resp.status != 200:
        if raise_error:
            m = "Cannot get upfile size: HTTP %d %s" % (resp.status, body[:128])
            raise RuntimeError(m)
        else:
            return -1

    content_range = resp.getheader('Content-Range')
    size = int(content_range.rsplit('/', 1)[1])
    return size

def download(url, fp, chunksize):
    conn = get_http_connection(url)
    upsize = get_upfile_size(conn)
    fp.seek(0, 2)
    localsize = fp.tell()

    offset = localsize
    while 1:
        if offset >= upsize:
            break
        end = offset + chunksize
        if end > upsize:
            end = upsize 
        byterange = 'bytes %d-%d/*' % (offset, end)
        conn.request('GET', conn.path, headers={'Content-Range': byterange})
        resp = conn.getresponse()
        data = resp.read()
        if resp.status != 200:
            m = "HTTP POST Failed: %d %s" % (resp.status, data[:128])
            raise RuntimeError(m)
        fp.write(data)
        offset += len(data)

def upload(fp, url, chunksize):
    conn = get_http_connection(url)
    upsize = get_upfile_size(conn, raise_error=0)
    if upsize < 0:
        upsize = 0
    fp.seek(0, 2)
    localsize = fp.tell()

    offset = upsize
    while 1:
        if offset >= localsize:
            break
        end = offset + chunksize
        if end > localsize:
            end = localsize
        chunksize = end - offset
        byterange = 'bytes %d-%d/*' % (offset, end)
        fp.seek(offset, 0)
        data = fp.read(chunksize)
        if len(data) != chunksize:
            m = "Unexpected short read"
            raise RuntimeError(m)
        conn.request('POST', conn.path, headers={'Content-Range': byterange}, body=data)
        resp = conn.getresponse()
        resp.read()
        if resp.status != 200:
            m = "HTTP POST Failed: %d" % resp.status
            raise RuntimeError(m)
        offset += chunksize


def usage():
    usage = """
    Usage: {0} download <url> <file> [chunksize_kb (def. 1024)]
           {0} upload <file> <url> [chunksize_kb (def. 128)]
    """
    print usage.format(argv[0])
    raise SystemExit


def main():
    argc = len(argv)

    if argc < 4:
        return usage()

    cmd = argv[1]
    if cmd == 'download':
        url = argv[2]
        filename = argv[3]
        fp = open(filename, "a")
        chunksize = int(argv[4]) * 1024 if argc > 4 else 1048576
        return download(url, fp, chunksize)
    elif cmd == 'upload':
        filename = argv[2]
        url = argv[3]
        fp = open(filename, "r")
        chunksize = int(argv[3]) * 1024 if argc > 4 else 131072
        return upload(fp, url, chunksize)

if __name__ == '__main__':
    main()
