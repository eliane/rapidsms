#!/usr/bin/env python
# vim: ai ts=4 sts=4 et sw=4 coding=utf-8

from datetime import date as pydate
from datetime import timedelta, datetime
import re
import copy

from django.contrib import admin
from django.db import models
from django.contrib.auth.models import User
from django.utils.translation import ugettext as _
from django.db.models.signals import pre_save, post_save, m2m_changed

from apps.reporters.models import *
from apps.locations.models import *

# CONFIGURATION
class Configuration(models.Model):

    id          = models.PositiveIntegerField(primary_key=True, default=1,editable=False)
    low_stock_level = models.PositiveIntegerField()

    def __unicode__(self):
        return "Configuration"

# EXCEPTIONS
class UnknownReporter(Exception):
    pass

class DuplicateReport(Exception):
    pass

class ErroneousDate(Exception):
    pass

class IncoherentValue(Exception):
    def __init__(self, message=_(u"FAILED. Sorry, your report seems to contain incoherent values. Please check and send again.")):
        self.message = message

class InvalidInput(Exception):
    pass

# REPORT PERIOD
class ReportPeriod(models.Model):

    class Meta:
        unique_together = ("start_date", "end_date")

    start_date  = models.DateTimeField()
    end_date    = models.DateTimeField()

    def __unicode__(self):
        return _(u"%(weekfmt)s") % {'weeknum': self.week, 'weekfmt': self.formated}

    @property
    def week(self):
        return int(self.start_date.strftime("%W"))

    @property
    def weeky(self):
        return self.date.strftime("%W.%y")

    @property
    def date(self):
        return self.start_date

    @property
    def formated(self):
        return _(u"%s-%s") % (self.start_date.strftime("%d"), self.end_date.strftime("%d/%m/%y"))

    @classmethod
    def current(cls):
        today   = pydate.today()
        return cls.from_day(today)

    @classmethod
    def from_day(cls, day):
        start, end  = cls.weekboundaries_from_day(day)
        try:
            return cls.objects.get(start_date=start, end_date=end)
        except cls.DoesNotExist:
            # create it and return
            period  = cls(start_date=start, end_date=end)
            period.save()
            return period

    @classmethod
    def from_index(cls, index):
        if index == 0:
            return cls.current()
        elif index > 0:
            ref_day     = pydate.today() - timedelta(index * 7)
            return cls.from_day(ref_day)
        else:
            raise InvalidInput

    @classmethod
    def weekboundaries_from_day(cls, day):
        if day.weekday() < 6:
            # get previous week
            delta   = 7 + day.weekday()
            startd  = day - timedelta(delta)
            endd    = startd + timedelta(6)
            start   = datetime(startd.year, startd.month, startd.day)
            end     = datetime(endd.year, endd.month, endd.day, 23, 59)
            return (start, end)
        else:
            # sunday can't bind
            raise ErroneousDate

# FIND REPORT (GENERIC)
class FindReport:

    TITLE   = _(u"Partial Report")

    @property
    def title(self):
        return self.TITLE

    @property
    def summary(self):
        return self.__unicode__()

# DISEASES
class Disease(models.Model):

    code    = models.CharField(max_length=2, unique=True)
    name    = models.CharField(max_length=50)

    def __unicode__(self):
        return self.name

    @classmethod
    def by_code(cls, code):
        return cls.objects.get(code=code)

class DiseaseObservation(models.Model):

    class Meta:
        unique_together = ("disease", "cases", "deaths")

    disease = models.ForeignKey(Disease)
    cases   = models.PositiveIntegerField()
    deaths  = models.PositiveIntegerField()

    def __unicode__(self):
        return _(u"%(code)s:%(cases)s/%(deaths)s") % {'code': self.disease.code.upper(), 'cases': self.cases, 'deaths': self.deaths}

    @classmethod
    def by_values(cls, disease, cases, deaths):
        try:
            return cls.objects.get(disease=disease, cases=cases, deaths=deaths)
        except cls.DoesNotExist:
            do = cls(disease=disease, cases=cases, deaths=deaths)
            do.save()
            return do

def DiseaseObservation_pre_save_handler(sender, **kwargs):

    instance    = kwargs['instance']

    if instance.deaths > instance.cases:
        raise IncoherentValue(_(u"FAILED: Deaths cannot be greater than cases.  Cases should include all deaths.  Please check and try again."))

pre_save.connect(DiseaseObservation_pre_save_handler, sender=DiseaseObservation)

class DiseasesReport(models.Model,FindReport):

    TITLE       = _(u"Diseases Report")

    class Meta:
        unique_together = ("reporter", "period")

    reporter    = models.ForeignKey(Reporter)
    period      = models.ForeignKey(ReportPeriod)

    
    diseases    = models.ManyToManyField(DiseaseObservation, blank=True, null=True)

    sent_on     = models.DateTimeField(auto_now_add=True)
    edited_by   = models.ForeignKey(User, blank=True, null=True)
    edited_on   = models.DateTimeField(blank=True, null=True)

    def __unicode__(self):
        cd  = self.cases_deaths
        return _(u"W%(week)s - %(clinic)s - %(cases)s+%(deaths)s") % {'week': self.period.week, 'clinic': self.reporter.location, 'cases': cd[0], 'deaths': cd[1]}

    def add(self, disease, cases=0, deaths=0):
        ''' add a disease declatation to the list '''

        obs = DiseaseObservation.by_values(disease=dis['disease'], cases=dis['cases'], deaths=dis['deaths'])
        self.diseases.add(obs)

    @property
    def summary(self):
        text    = str()
        for dis in self.diseases.all():
            if dis.cases > 0:
                text += _(u"%(disease)s ") % {'disease': dis}
        return text[:-1]

    @property
    def cases_deaths(self):
        cases   = 0
        deaths  = 0
        for obs in self.diseases.all():
            cases   += obs.cases
            deaths  += obs.deaths

        return cases, deaths

    @property
    def cases(self):
        return self.cases_deaths[0]

    @property
    def deaths(self):
        return self.cases_deaths[1]

    def reset(self):
        return self.diseases.clear()

    @classmethod
    def by_reporter_period(cls, reporter, period):
        try:
            return cls.objects.get(reporter=reporter, period=period)
        except cls.DoesNotExist:
            report  = cls(reporter=reporter, period=period)
            report.save()
            return report

    @classmethod
    def total_cases(cls, disease, period, location):
        total   = 0
        reports = cls.objects.filter(period=period, location_in=location_parents(location))
        for report in reports:
            try:
                total   += report.diseases.get(disease=disease).cases
            except:
                pass
        return total

def DiseasesReport_m2m_changed_handler(sender, **kwargs):

    instance    = kwargs['instance']
    action      = kwargs['action']

    if action == 'add':

        diseases    = []
        for obs in instance.diseases.all():
            if diseases.count(obs.disease) > 0:
                instance.delete()
                raise IncoherentValue(_(u'FAILED: Duplicate disease codes received. Please check and try again.'))
            else:
                diseases.append(obs.disease)

m2m_changed.connect(DiseasesReport_m2m_changed_handler, sender=DiseasesReport)

# MALARIA CASES
class MalariaCasesReport(models.Model,FindReport):

    TITLE       = _(u"Malaria Cases Report")

    class Meta:
        unique_together = ("reporter", "period")

    reporter    = models.ForeignKey(Reporter)
    period      = models.ForeignKey(ReportPeriod)

    _opd_attendance      = models.PositiveIntegerField(default=0, verbose_name=_(u"OPD Attendance"))
    _suspected_cases     = models.PositiveIntegerField(default=0, verbose_name=_(u"Suspected Cases"))
    _rdt_tests           = models.PositiveIntegerField(default=0, verbose_name=_(u"RDT Tests"))
    _rdt_positive_tests  = models.PositiveIntegerField(default=0, verbose_name=_(u"RDT+ Tested"))
    _microscopy_tests    = models.PositiveIntegerField(default=0, verbose_name=_(u"Microscopy Tests"))
    _microscopy_positive = models.PositiveIntegerField(default=0, verbose_name=_(u"Microscopy+ Tested"))
    _positive_under_five = models.PositiveIntegerField(default=0, verbose_name=_(u"Positives under 5"))
    _positive_over_five  = models.PositiveIntegerField(default=0, verbose_name=_(u"Positives over 5"))

    sent_on     = models.DateTimeField(auto_now_add=True)
    edited_by   = models.ForeignKey(User, blank=True, null=True)
    edited_on   = models.DateTimeField(blank=True, null=True)

    def __unicode__(self):
        return _(u"W%(week)s - %(clinic)s") % {'week': self.period.week, 'clinic': self.reporter.location}

    def reset(self):
        self._opd_attendance     = 0
        self._suspected_cases    = 0
        self._rdt_tests          = 0
        self._rdt_positive_tests = 0
        self._microscopy_tests   = 0
        self._microscopy_positive= 0
        self._positive_under_five= 0
        self._positive_over_five = 0

    @classmethod
    def by_reporter_period(cls, reporter, period):
        try:
            return cls.objects.get(reporter=reporter, period=period)
        except cls.DoesNotExist:
            report  = cls(reporter=reporter, period=period)
            report.save()
            return report

    def update(self, opd_attendance, suspected_cases, rdt_tests, rdt_positive_tests, microscopy_tests, microscopy_positive, positive_under_five, positive_over_five):
        ''' saves all datas at once '''

        self.opd_attendance     = opd_attendance
        self.suspected_cases    = suspected_cases
        self.rdt_tests          = rdt_tests
        self.rdt_positive_tests = rdt_positive_tests
        self.microscopy_tests   = microscopy_tests
        self.microscopy_positive= microscopy_positive
        self.positive_under_five= positive_under_five
        self.positive_over_five = positive_over_five
        self.save()

    # opd_attendance property
    def get_opd_attendance(self):
        return self._opd_attendance
    def set_opd_attendance(self, value):
        self._opd_attendance = value
    opd_attendance  = property(get_opd_attendance, set_opd_attendance)

    # suspected_cases property
    def get_suspected_cases(self):
        return self._suspected_cases
    def set_suspected_cases(self, value):
        if value > self.opd_attendance:
            raise IncoherentValue(_("FAILED: Suspected malaria cases cannot be greater than total OPD attendance. Please check and try again."))
        self._suspected_cases = value
    suspected_cases  = property(get_suspected_cases, set_suspected_cases)

    # rdt_tests property
    def get_rdt_tests(self):
        return self._rdt_tests
    def set_rdt_tests(self, value):
        if value > self.opd_attendance:
            raise IncoherentValue(_("FAILED: RDT tested cases cannot be greater than total OPD attendance. Please check and try again."))
        if value > self.suspected_cases:
            raise IncoherentValue(_("FAILED: RDT tested cases cannot be greater than suspected malaria cases. Please check and try again."))
        self._rdt_tests = value
    rdt_tests  = property(get_rdt_tests, set_rdt_tests)

    # rdt_positive_tests property
    def get_rdt_positive_tests(self):
        return self._rdt_positive_tests
    def set_rdt_positive_tests(self, value):
        if value > self.rdt_tests:
            raise IncoherentValue(_(u"FAILED: RDT positive cases cannot be greater than RDT tested cases. Please check and try again."))
        self.__rdt_positive_tests = value
    rdt_positive_tests  = property(get_rdt_positive_tests, set_rdt_positive_tests)

    # microscopy_tests property
    def get_microscopy_tests(self):
        return self._microscopy_tests
    def set_microscopy_tests(self, value):
        if value > self.opd_attendance:
            raise IncoherentValue(_("FAILED: Microscopy tested cases cannot be greater than total OPD attendance. Please check and try again."))
        if value > self.suspected_cases:
            raise IncoherentValue(_("FAILED: Microscopy tested cases cannot be greater than suspected malaria cases. Please check and try again."))
        self._microscopy_tests = value
    microscopy_tests  = property(get_microscopy_tests, set_microscopy_tests)

    # microscopy_positive property
    def get_microscopy_positive(self):
        return self._microscopy_positive
    def set_microscopy_positive(self, value):
        if value > self.microscopy_tests:
            raise IncoherentValue(_(u"FAILED: Microscopy positive cases cannot be greater than microscopy tested cases. Please check and try again."))
        self.__microscopy_positive = value
    microscopy_positive  = property(get_microscopy_positive, set_microscopy_positive)

    # positive_under_five property
    def get_positive_under_five(self):
        return self._positive_under_five
    def set_positive_under_five(self, value):
        self.__positive_under_five = value
    positive_under_five  = property(get_positive_under_five, set_positive_under_five)

    # property
    def get_positive_over_five(self):
        return self._positive_over_five
    def set_positive_over_five(self, value):
        if (value + self.positive_under_five) > self.suspected_cases:
            raise IncoherentValue(_(u"FAILED: Positive cases cannot be greater than suspected malaria cases. Please check and try again."))
        if (value + self.positive_under_five) > self.opd_attendance:
            raise IncoherentValue(_(u"FAILED: Positive cases cannot be greater than total OPD attendance. Please check and try again."))
        self.__positive_over_five = value
    positive_over_five  = property(get_positive_over_five, set_positive_over_five)

    @property
    def summary(self):
        text    = _(u"OPD: %(opd_attendance)s, SUSPECT: %(suspected_cases)s, RDT: %(rdt_tests)s, RDT.POS: %(rdt_positive_tests)s, MICROS: %(microscopy_tests)s, MICROS+: %(microscopy_positive)s, 0-5 POS: %(positive_under_five)s, 5+ POS: %(positive_over_five)s") % {'opd_attendance': self.opd_attendance, 'suspected_cases': self.suspected_cases, 'rdt_tests': self.rdt_tests, 'rdt_positive_tests': self.rdt_positive_tests, 'microscopy_tests': self.microscopy_tests, 'microscopy_positive': self.microscopy_positive, 'positive_under_five': self.positive_under_five, 'positive_over_five': self.positive_over_five}
        return text

def MalariaCasesReport_pre_save_handler(sender, **kwargs):

    instance    = kwargs['instance']

    instcopy    = copy.copy(instance)
    instcopy.reset()

    # call all setters
    instcopy.opd_attendance     = instance.opd_attendance
    instcopy.suspected_cases    = instance.suspected_cases
    instcopy.rdt_tests          = instance.rdt_tests
    instcopy.rdt_positive_tests = instance.rdt_positive_tests
    instcopy.microscopy_tests   = instance.microscopy_tests
    instcopy.microscopy_positive= instance.microscopy_positive
    instcopy.positive_under_five= instance.positive_under_five
    instcopy.positive_over_five = instance.positive_over_five

    if (instance.positive_under_five + instance.positive_over_five) > (instance.microscopy_positive + instance.rdt_positive_tests):
        raise IncoherentValue(_(u"FAILED: The sum of positive cases cannot be greater than the sum of RDT positive cases and Microscopy positive cases."))

pre_save.connect(MalariaCasesReport_pre_save_handler, sender=MalariaCasesReport)

# MALARIA TREATMENTS
class MalariaTreatmentsReport(models.Model,FindReport):

    TITLE       = _(u"Malaria Treatments Report")

    class Meta:
        unique_together = ("reporter", "period")

    reporter    = models.ForeignKey(Reporter)
    period      = models.ForeignKey(ReportPeriod)

    _rdt_positive        = models.PositiveIntegerField(default=0, verbose_name=_(u"RDT+ Cases Treated"))
    _rdt_negative        = models.PositiveIntegerField(default=0, verbose_name=_(u"RDT- Cases Treated"))
    _four_months_to_three= models.PositiveIntegerField(default=0, verbose_name=_(u"4 months - 3 years"))
    _three_to_seven      = models.PositiveIntegerField(default=0, verbose_name=_(u"3 - 7 years"))
    _seven_to_twelve     = models.PositiveIntegerField(default=0, verbose_name=_(u"7 - 12 years"))
    _twelve_and_above    = models.PositiveIntegerField(default=0, verbose_name=_(u"12 years & above"))


    sent_on     = models.DateTimeField(auto_now_add=True)
    edited_by   = models.ForeignKey(User, blank=True, null=True)
    edited_on   = models.DateTimeField(blank=True, null=True)

    def __unicode__(self):
        return _(u"W%(week)s - %(clinic)s") % {'week': self.period.week, 'clinic': self.reporter.location}

    def reset(self):
        self._rdt_positive           = 0
        self._rdt_negative           = 0
        self._four_months_to_three   = 0
        self._three_to_seven         = 0
        self._seven_to_twelve        = 0
        self._twelve_and_above       = 0

    @classmethod
    def by_reporter_period(cls, reporter, period):
        try:
            return cls.objects.get(reporter=reporter, period=period)
        except cls.DoesNotExist:
            report  = cls(reporter=reporter, period=period)
            report.save()
            return report

    def update(self, rdt_positive, rdt_negative, four_months_to_three, three_to_seven, seven_to_twelve, twelve_and_above):
        ''' saves all datas at once '''

        self.rdt_positive           = rdt_positive
        self.rdt_negative           = rdt_negative
        self.four_months_to_three   = four_months_to_three
        self.three_to_seven         = three_to_seven
        self.seven_to_twelve        = seven_to_twelve
        self.twelve_and_above       = twelve_and_above
        self.save()

    # rdt_positive property
    def get_rdt_positive(self):
        return self._rdt_positive
    def set_rdt_positive(self, value):
        self._rdt_positive = value
    rdt_positive  = property(get_rdt_positive, set_rdt_positive)

    # rdt_negative property
    def get_rdt_negative(self):
        return self._rdt_negative
    def set_rdt_negative(self, value):
        self._rdt_negative = value
    rdt_negative  = property(get_rdt_negative, set_rdt_negative)

    # four_months_to_three property
    def get_four_months_to_three(self):
        return self._four_months_to_three
    def set_four_months_to_three(self, value):
        self._four_months_to_three = value
    four_months_to_three  = property(get_four_months_to_three, set_four_months_to_three)

    # three_to_seven property
    def get_three_to_seven(self):
        return self._three_to_seven
    def set_three_to_seven(self, value):
        self._three_to_seven = value
    three_to_seven  = property(get_three_to_seven, set_three_to_seven)

    # seven_to_twelve property
    def get_seven_to_twelve(self):
        return self._seven_to_twelve
    def set_seven_to_twelve(self, value):
        self._seven_to_twelve = value
    seven_to_twelve  = property(get_seven_to_twelve, set_seven_to_twelve)

    # twelve_and_above property
    def get_twelve_and_above(self):
        return self._twelve_and_above
    def set_twelve_and_above(self, value):
        self._twelve_and_above = value
    twelve_and_above  = property(get_twelve_and_above, set_twelve_and_above)

    @property
    def summary(self):
        text    = _(u"RDT.POS: %(rdt_positive)s, RDT.NEG: %(rdt_negative)s, 4M-3Y: %(four_months_to_three)s, 3Y-7Y: %(three_to_seven)s, 7Y-12Y+: %(seven_to_twelve)s, 12Y+: %(twelve_and_above)s") % {'rdt_positive': self.rdt_positive, 'rdt_negative': self.rdt_negative, 'four_months_to_three': self.four_months_to_three, 'three_to_seven': self.three_to_seven, 'seven_to_twelve': self.seven_to_twelve, 'twelve_and_above': self.twelve_and_above}
        return text

def MalariaTreatmentsReport_pre_save_handler(sender, **kwargs):

    instance    = kwargs['instance']

    if (instance.rdt_positive + instance.rdt_negative) > (instance.four_months_to_three + instance.three_to_seven + instance.seven_to_twelve + instance.twelve_and_above):
        raise IncoherentValue(_(u"FAILED: The sum of RDT negative cases treated and RDT positive cases treated cannot be greater than the sum of age groups treated."))

pre_save.connect(MalariaTreatmentsReport_pre_save_handler, sender=MalariaTreatmentsReport)


# ACT CONSUMPTION
class ACTConsumptionReport(models.Model,FindReport):

    TITLE       = _(u"ACT Stock Data")

    class Meta:
        unique_together = ("reporter", "period")

    reporter    = models.ForeignKey(Reporter)
    period      = models.ForeignKey(ReportPeriod)

    _yellow_dispensed    = models.PositiveIntegerField(default=0, verbose_name=_(u"Yellow dispensed"))
    _yellow_balance      = models.PositiveIntegerField(default=0, verbose_name=_(u"Yellow balance"))
    _blue_dispensed      = models.PositiveIntegerField(default=0, verbose_name=_(u"Blue dispensed"))
    _blue_balance        = models.PositiveIntegerField(default=0, verbose_name=_(u"Blue balance"))
    _brown_dispensed     = models.PositiveIntegerField(default=0, verbose_name=_(u"Brown dispensed"))
    _brown_balance       = models.PositiveIntegerField(default=0, verbose_name=_(u"Brown balance"))
    _green_dispensed     = models.PositiveIntegerField(default=0, verbose_name=_(u"Green dispensed"))
    _green_balance       = models.PositiveIntegerField(default=0, verbose_name=_(u"Green balance"))
    _other_act_dispensed = models.PositiveIntegerField(default=0, verbose_name=_(u"Other ACT dispensed"))
    _other_act_balance   = models.PositiveIntegerField(default=0, verbose_name=_(u"Other ACT balance"))

    sent_on     = models.DateTimeField(auto_now_add=True)
    edited_by   = models.ForeignKey(User, blank=True, null=True)
    edited_on   = models.DateTimeField(blank=True, null=True)

    def __unicode__(self):
        return _(u"W%(week)s - %(clinic)s") % {'week': self.period.week, 'clinic': self.reporter.location}

    def reset(self):
        self._yellow_dispensed    = 0
        self._yellow_balance      = 0
        self._blue_dispensed      = 0
        self._blue_balance        = 0
        self._brown_dispensed     = 0
        self._brown_balance       = 0
        self._green_dispensed     = 0
        self._green_balance       = 0
        self._other_act_dispensed = 0
        self._other_act_balance   = 0

    @classmethod
    def by_reporter_period(cls, reporter, period):
        try:
            return cls.objects.get(reporter=reporter, period=period)
        except cls.DoesNotExist:
            report  = cls(reporter=reporter, period=period)
            report.save()
            return report

    def update(self, yellow_dispensed, yellow_balance, blue_dispensed, blue_balance, brown_dispensed, brown_balance, green_dispensed, green_balance,  other_act_dispensed, other_act_balance):
        ''' saves all datas at once '''

        self.yellow_dispensed    = yellow_dispensed
        self.yellow_balance      = yellow_balance
        self.blue_dispensed      = blue_dispensed
        self.blue_balance        = blue_balance
        self.brown_dispensed     = brown_dispensed
        self.brown_balance       = brown_balance
        self.green_dispensed     = green_dispensed
        self.green_balance       = green_balance
        self.other_act_dispensed = other_act_dispensed
        self.other_act_balance   = other_act_balance
        self.save()

    # yellow_dispensed property
    def get_yellow_dispensed(self):
        return self._yellow_dispensed
    def set_yellow_dispensed(self, value):
        self._yellow_dispensed = value
    yellow_dispensed  = property(get_yellow_dispensed, set_yellow_dispensed)

    # yellow_balance property
    def get_yellow_balance(self):
        return self._yellow_balance
    def set_yellow_balance(self, value):
        self._yellow_balance = value
    yellow_balance  = property(get_yellow_balance, set_yellow_balance)

    # blue_dispensed property
    def get_blue_dispensed(self):
        return self._blue_dispensed
    def set_blue_dispensed(self, value):
        self._blue_dispensed = value
    blue_dispensed  = property(get_blue_dispensed, set_blue_dispensed)

    # blue_balance property
    def get_blue_balance(self):
        return self._blue_balance
    def set_blue_balance(self, value):
        self._blue_balance = value
    blue_balance  = property(get_blue_balance, set_blue_balance)

    # brown_dispensed property
    def get_brown_dispensed(self):
        return self._brown_dispensed
    def set_brown_dispensed(self, value):
        self._brown_dispensed = value
    brown_dispensed  = property(get_brown_dispensed, set_brown_dispensed)

    # brown_balance property
    def get_brown_balance(self):
        return self._brown_balance
    def set_brown_balance(self, value):
        self._brown_balance = value
    brown_balance  = property(get_brown_balance, set_brown_balance)

    # green_dispensed property
    def get_green_dispensed(self):
        return self._green_dispensed
    def set_green_dispensed(self, value):
        self._green_dispensed = value
    green_dispensed  = property(get_green_dispensed, set_green_dispensed)

    # green_balance property
    def get_green_balance(self):
        return self._green_balance
    def set_green_balance(self, value):
        self._green_balance = value
    green_balance  = property(get_green_balance, set_green_balance)

    # other_act_dispensed property
    def get_other_act_dispensed(self):
        return self._other_act_dispensed
    def set_other_act_dispensed(self, value):
        self._other_act_dispensed = value
    other_act_dispensed  = property(get_other_act_dispensed, set_other_act_dispensed)

    # other_act_balance property
    def get_other_act_balance(self):
        return self._other_act_balance
    def set_other_act_balance(self, value):
        self._other_act_balance = value
    other_act_balance  = property(get_other_act_balance, set_other_act_balance)

    @classmethod
    def bool_to_stock(cls, boolean):
        return _(u"Y") if boolean else _(u"N")

    @classmethod
    def text_to_stock(cls, text):
        return text.lower() == 'y'

    @property
    def summary(self):
        text    = _(u"YELLOW: %(yellow_dispensed)s/%(yellow_balance)s, BLUE: %(blue_dispensed)s/%(blue_balance)s, BROWN: %(brown_dispensed)s/%(brown_balance)s, GREEN: %(green_dispensed)s/%(green_balance)s, OTHER.ACT: %(other_act_dispensed)s/%(other_act_balance)s") % {'yellow_dispensed': self.yellow_dispensed, 'yellow_balance': self.yellow_balance, 'blue_dispensed': self.blue_dispensed, 'blue_balance': self.blue_balance, 'brown_dispensed': self.brown_dispensed, 'brown_balance': self.brown_balance, 'green_dispensed': self.green_dispensed, 'green_balance': self.green_balance, 'other_act_dispensed': self.other_act_dispensed, 'other_act_balance': self.other_act_balance}
        return text

# EPIDEMIOLOGICAL REPORT
class EpidemiologicalReport(models.Model):

    TITLE       = _(u"Epidemiological Report")

    STATUS_STARTED  = 0
    STATUS_COMPLETED= 1

    STATUSES        = (
        (STATUS_STARTED, _(u"Started")),
        (STATUS_COMPLETED, _(u"Completed")),
    )

    class Meta:
        unique_together = ("clinic", "period")

    clinic  = models.ForeignKey(Location, related_name='clinic')
    period  = models.ForeignKey(ReportPeriod, related_name='period')

    _diseases            = models.ForeignKey(DiseasesReport, null=True, blank=True, related_name='diseases_report', verbose_name=DiseasesReport.TITLE)
    _malaria_cases       = models.ForeignKey(MalariaCasesReport, null=True, blank=True, related_name='cases_report', verbose_name=MalariaCasesReport.TITLE)
    _malaria_treatments  = models.ForeignKey(MalariaTreatmentsReport, null=True, blank=True, related_name='treatments_report', verbose_name=MalariaTreatmentsReport.TITLE)
    _act_consumption     = models.ForeignKey(ACTConsumptionReport, null=True, blank=True, related_name='act_report', verbose_name=ACTConsumptionReport.TITLE)
    _remarks             = models.CharField(max_length=160, blank=True, null=True, verbose_name=_(u"Remarks"))

    _status      = models.CharField(max_length=1, choices=STATUSES,default=STATUS_STARTED)
    started_on  = models.DateTimeField(auto_now_add=True)
    completed_on= models.DateTimeField(null=True, blank=True)

    def __unicode__(self):
        return _(u"W%(week)s - %(clinic)s - %(completion)s") % {'week': self.period.weeky, 'clinic': self.clinic, 'completion': self.quarters}

    # status property
    def get_status(self):
        return int(self._status)
    def set_status(self, value):
        self._status = value.__str__()
    status  = property(get_status, set_status)

    # diseases property
    def get_diseases(self):
        return self._diseases
    def set_diseases(self, value):
        if value.reporter.location != self.clinic or value.period != self.period:
            raise IncoherentValue
        self._diseases = value
    diseases  = property(get_diseases, set_diseases)

    # malaria_cases property
    def get_malaria_cases(self):
        return self._malaria_cases
    def set_malaria_cases(self, value):
        if value.reporter.location != self.clinic or value.period != self.period:
            raise IncoherentValue
        self._malaria_cases = value
    malaria_cases  = property(get_malaria_cases, set_malaria_cases)

    # malaria_treatments property
    def get_malaria_treatments(self):
        return self._malaria_treatments
    def set_malaria_treatments(self, value):
        if value.reporter.location != self.clinic or value.period != self.period:
            raise IncoherentValue
        self._malaria_treatments = value
    malaria_treatments  = property(get_malaria_treatments, set_malaria_treatments)

    # act_consumption property
    def get_act_consumption(self):
        return self._act_consumption
    def set_act_consumption(self, value):
        if value.reporter.location != self.clinic or value.period != self.period:
            raise IncoherentValue
        self._act_consumption = value
    act_consumption  = property(get_act_consumption, set_act_consumption)

    # remarks property
    def get_remarks(self):
        return self._remarks
    def set_remarks(self, value):
        self._remarks = value
    remarks  = property(get_remarks, set_remarks)

    @classmethod
    def by_clinic_period(cls, clinic, period):
        try:
            return cls.objects.get(clinic=clinic, period=period)
        except cls.DoesNotExist:
            report  = cls(clinic=clinic, period=period)
            report.save()
            return report

    @classmethod
    def by_receipt(cls, receipt):
        clinic_id, week_id, date, report_id = re.search('([0-9]+)W([0-9]+)\-([0-9]{6})\/([0-9]+)', receipt).groups()
        return cls.objects.get(clinic=Location.objects.get(id=clinic_id), period=ReportPeriod.objects.get(id=week_id), id=report_id)

    @property
    def verbose_status(self):
        for status, text in self.STATUSES:
            if status == self.status: return text

    @property
    def receipt(self):
        if self.completed and self.completed_on:
            return _(u"%(clinic)sW%(week)s-%(datesent)s/%(reportid)s") % {'clinic': self.clinic.id, 'datesent': self.completed_on.strftime("%d%m%y"), 'week': self.period.id, 'reportid': self.id}
        else:
            return None

    @property
    def completion(self):
        comp    = 0
        if self.diseases:           comp += 0.25
        if self.malaria_cases:      comp += 0.25
        if self.malaria_treatments: comp += 0.25
        if self.act_consumption:    comp += 0.25

        return comp

    @property
    def completed(self):
        return self.status == self.STATUS_COMPLETED

    @property
    def complete(self):
        return self.completion == 1

    @property
    def quarters(self):
        comp    = self.completion
        stages  = 4
        return _(u"%(completed)s/%(total)s") % {'completed': int(comp * stages), 'total': int(stages)}

    @property
    def title(self):
        return self.TITLE

    @property
    def summary(self):
        def bool_to_text(boolean):
            return _(u"Y") if boolean else _(u"N")

        def text_to_bool(text):
            return text.lower() == 'y'

        return _(u"%(diseases)s:%(diseases_done)s, %(malaria_cases)s:%(malaria_cases_done)s, %(malaria_treatments)s:%(malaria_treatments_done)s, %(act_consumption)s:%(act_consumption_done)s, %(remarks)s:%(remarks_done)s (%(comp)s)") % {'comp': self.quarters, 'diseases': DiseasesReport.TITLE, 'diseases_done': bool_to_text(self.diseases), 'malaria_cases': MalariaCasesReport.TITLE, 'malaria_cases_done': bool_to_text(self.malaria_cases), 'malaria_treatments': MalariaTreatmentsReport.TITLE, 'malaria_treatments_done': bool_to_text(self.malaria_treatments), 'act_consumption': ACTConsumptionReport.TITLE, 'act_consumption_done': bool_to_text(self.act_consumption), 'remarks': "Remarks", 'remarks_done': bool_to_text(self.remarks)}

def EpidemiologicalReport_pre_save_handler(sender, **kwargs):

    instance    = kwargs['instance']

    for value in (instance.diseases, instance.malaria_cases, instance.malaria_treatments, instance.act_consumption):
        try:
            if value.reporter.location != instance.clinic or value.period != instance.period:
                raise IncoherentValue
        except AttributeError:
            pass

pre_save.connect(EpidemiologicalReport_pre_save_handler, sender=EpidemiologicalReport)

# ALERTS

def location_parents(location):

    ancestors   = []
    ancestors.append(location)

    while (location.parent):
        ancestors.append(location.parent)

    return ancestors

class DiseaseAlertTrigger(models.Model):

    disease     = models.ForeignKey(Disease)
    threshold   = models.PositiveIntegerField()
    location    = models.ForeignKey(Location)
    subscribers = models.ManyToManyField(Reporter)

    def test(self, period, location):
        ''' test validity of Alert Trigger with values '''

        # check existence of similar alert
        try:
            existing    = DiseaseAlert.by_period_trigger(period=period, trigger=trigger)
            if existing: return False
        except:
            pass

        if not self.location in location_parents(location):
            return False

        total   = DiseasesReport.total_cases(disease=self.disease, period=period, location=self.location)
        if total >= self.threshold:
            return total
        else:
            return False
    

    def raise_alert(self, period, location):
        ''' create an Alert object from values '''

        total   = self.test(period, location, value)
        if not total:
            return False

        alert   = DiseaseAlert(period=period, trigger=self, value=total, recipients=self.recipients)
        alert.save()

        return alert

    @property
    def recipients(self):
        ''' list of subscribers who should receive alert now '''

        recipients  = []
        for subscriber in self.subscribers:
            if subscriber.registered_self and ReporterGroup.objects.get(title='alerts') in subscriber.groups.only():
                recipients.append(subscriber)
        return recipients

    

class DiseaseAlert(models.Model):

    STATUS_STARTED  = 0
    STATUS_COMPLETED= 1

    STATUSES        = (
        (STATUS_STARTED, _(u"Started")),
        (STATUS_COMPLETED, _(u"Completed")),
    )

    class Meta:
        unique_together = ("period", "trigger")

    period      = models.ForeignKey(ReportPeriod)
    trigger     = models.ForeignKey(DiseaseAlertTrigger)

    value       = models.PositiveIntegerField()
    status      = models.CharField(max_length=1, choices=STATUSES,default=STATUS_STARTED)
    recipients  = models.ManyToManyField(Reporter)

    started_on  = models.DateTimeField(auto_now_add=True)
    completed_on= models.DateTimeField(null=True, blank=True)

    @classmethod
    def by_period_trigger(cls, period, trigger):

        return cls.objects.get(period=period, trigger=trigger)



