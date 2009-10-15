#!/usr/bin/env python
# vim: ai ts=4 sts=4 et sw=4 coding=utf-8

from django.contrib import admin
from models import *
from django.utils.translation import ugettext_lazy as _

admin.site.register(Configuration)
admin.site.register(RDTStockAlert)
