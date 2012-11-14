# -*- coding: utf-8 -*-
from django.conf.urls.defaults import patterns
from views import upfile_view

urlpatterns = patterns('',
  (r'^(?P<filename>[^/]+)$', upfile_view),
)


