
from django.http import (HttpResponse,
                         HttpResponseNotAllowed,
                         HttpResponseNotFound,
                         HttpResponseBadRequest)
from .models import Upfile

def upfile_view(request, filename=None):
    method = request.method
    if method == 'HEAD':
        return get_upfile(request, filename=filename, head=1)
    elif method == 'GET':
        return get_upfile(request, filename=filename, head=0)
    elif method == 'POST': 
        return post_upfile(request, filename=filename)
    else:
        return HttpResponseNotAllowed(['HEAD', 'GET', 'POST'])

def _get_range(content_range):
    if '=' in content_range:
        b, r = content_range.split('=')
    else:
        b, r = content_range.split()
    if b != 'bytes':
        return HttpResponseBadRequest('invalid content range unit')

    r, sep, total = r.partition('/')
    if r == '*':
        return HttpResponseBadRequest('invalid content range')
    start, sep, end = r.partition('-')
    try:
        start = int(start)
        end = int(end) + 1 if end else None
    except ValueError:
        return HttpResponseBadRequest('invalid content range spec')

    return start, end

def post_upfile(request, filename=None):
    meta = request.META
    data = request.body
    content_range = meta.get('HTTP_CONTENT_RANGE', None)
    if content_range is None:
        offset = 0
    else:
        r = _get_range(content_range)
        if isinstance(r, HttpResponse):
            return r

        offset, end = r
        if end - offset != len(data):
            return HttpResponseBadRequest('range size mismatch')

    if not filename:
        return HttpResponseBadRequest('Malformed path')

    try:
        upfile = Upfile.objects.get(filename=filename)
    except Upfile.DoesNotExist:
        upfile = Upfile.objects.create(filename=filename)
        #return HttpResponse('Upfile not found', status=404)

    size = upfile.get_size()
    if size < 0:
        size = 0

    try:
        r = upfile.write(offset, data)
    except ValueError:
        return HttpResponse('invalid offset %d != %d' % (size, offset),
                            status=412)
    return HttpResponse('OK', status=200)

def get_upfile(request, filename=None, head=0):
    meta = request.META
    data = request.body
    content_range = meta.get('HTTP_RANGE', None)
    if content_range is None:
        content_range = meta.get('HTTP_CONTENT_RANGE', None)
    if content_range is None:
        offset = 0
        end = None
    else:
        r = _get_range(content_range)
        if isinstance(r, HttpResponse):
            return r
        offset, end = r

    if not filename:
        return HttpResponse('Malformed path', status=404)

    try:
        upfile = Upfile.objects.get(filename=filename)
    except Upfile.DoesNotExist:
        upfile = Upfile.objects.create(filename=filename)
        #return HttpResponse('Upfile not found', status=404)

    size = upfile.get_size()
    if end is None or end > size:
        end = size

    response = HttpResponse()
    response.status_code = 200
    response['Content-Range'] = 'bytes %d-%d/%d' % (offset, end-1, size)
    if not head:
        upfile.seek(offset)
        response.content = upfile.read(end - offset)

    return response

