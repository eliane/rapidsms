#!/usr/bin/env python
# vim: ai ts=4 sts=4 et sw=4

from django.contrib import admin

from models import ReportMalnutrition

class ReportMalnutritionAdmin(admin.ModelAdmin):
    list_display = ("case", "name","zone", "muac", "entered_at","reporter")
    search_fields = ['case__ref_id' ]

admin.site.register(ReportMalnutrition, ReportMalnutritionAdmin)

