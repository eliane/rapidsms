#!/usr/bin/env python
# vim: ai ts=4 sts=4 et sw=4 coding=utf-8

from django.db import models
from django.utils.translation import ugettext as _

from reporters.models import Role, ReporterGroup, Reporter, PersistantConnection
from locations.models import Location, LocationType, RecursiveManager

#def _(txt): return txt

from django.contrib.auth.models import User, Group

import rapidsms
from rapidsms.parsers.keyworder import Keyworder
from rapidsms.message import Message
from rapidsms.connection import Connection

from models.logs import MessageLog, log, elog

from models.general import Provider, User
from models.general import Facility, Case, CaseNote, Zone


import re, time, datetime


def authenticated (func):
    def wrapper (self, message, *args):
        if message.sender:
            return func(self, message, *args)
        else:
            message.respond(_("%s is not a registered number.")
                            % message.peer)
            return True
    return wrapper

class HandlerFailed (Exception):
    pass

def registered (func):
    def wrapper (self, message, *args):
        if message.persistant_connection.reporter:
            return func(self, message, *args)
        else:
            message.respond(_(u"Sorry, only registered users can access this program."))
            return True
    return wrapper

class App (rapidsms.app.App):
    MAX_MSG_LEN = 140
    keyword = Keyworder()

    def start (self):
        """Configure your app in the start phase."""
        pass

    def parse (self, message):
        """parser """
        # allow authentication to occur when http tester is used
        if message.peer[:3] == '233':
            mobile = "+" + message.peer
        else:
            mobile = message.peer 
        provider = Provider.by_mobile(mobile)
        if provider:
            message.sender = provider.user
        else:
            message.sender = None
        message.was_handled = False

    def cleanup (self, message):
        """cleanup """
        log = MessageLog(mobile=message.peer,
                         sent_by=message.persistant_connection.reporter,
                         text=message.text,
                         was_handled=message.was_handled)
        log.save()

    def handle (self, message):
        """Handles things. """
        try:
            func, captures = self.keyword.match(self, message.text)
        except TypeError:
            #message.respond(dir(self))
            
            #If the command included 'join', respond by reminding the user of the correct format for the command           
            mctc_input = message.text.lower()
            if not (mctc_input.find("join") == -1):
                message.respond(self.get_join_format_reminder())
                return True

            if mctc_input.find("transfer") > -1:
                message.respond(self.get_transfer_format_reminder())
                return True

            if mctc_input.find("new") > -1:
                message.respond(self.get_new_patient_format_reminder())
                return True

            #command_list = [method for method in dir(self) if callable(getattr(self, method))]
            #info_list = [str(method) + "--->" + str(getattr(self, method).__doc__) + "\n" for method in dir(self) if callable(getattr(self, method))]
            #message.respond(info_list)
            message.respond(_("Sorry Unknown command: '%(msg)s...' Please try again") % {"msg":message.text[:20]})
            
            return False
        try:
            handled = func(self, message, *captures)
        except HandlerFailed, e:
            message.respond(e.message)
            
            handled = True
        except Exception, e:
            # TODO: log this exception
            # FIXME: also, put the contact number in the config
            message.respond(_("An error occurred. Please call 0733202270."))
            
            elog(message.sender, message.text)
            raise
        message.was_handled = bool(handled)
        return handled

    def get_join_format_reminder(self):
        """Expected format for join command, sent as a reminder"""
        return "Format:  join [location] [your last name] [your first name] (your role - leave blank for CHEW)"

    @keyword("join (\S+) (\S+) (\S+)(?: ([a-z]\w+))?")
    def join (self, message, location_code, last_name, first_name, role=None):
        """ Format <<<<<<>>>>>>join [location code] [last name] [first name] [role - leave blank for CHEW]<>
            Purpose: register as a user and join the system """

        #default alias for everyone until further notice
        username=None        
        # do not skip roles for now
        role_code   = role
        try:
            name = "%s %s"%(first_name, last_name)
            # parse the name, and create a reporter            
            alias, fn, ln = Reporter.parse_name(name)

            if not message.persistant_connection.reporter:
                rep = Reporter(alias=alias, first_name=fn, last_name=ln)
            else:
                rep = message.persistant_connection.reporter
                rep.alias       = alias
                rep.first_name  = fn
                rep.last_name   = ln

            rep.save()
            
            # attach the reporter to the current connection
            message.persistant_connection.reporter = rep
            message.persistant_connection.save()
                  
            # something went wrong - at the
            # moment, we don't care what
        except:
            message.respond("Join Error. Unable to register your account.")
            
        if role_code == None or role_code.__len__() < 1:
            role_code   = 'chw'

        reporter    = message.persistant_connection.reporter
        
        # check location code
        try:
            location  = Location.objects.get(code=location_code)
        except models.ObjectDoesNotExist:
            message.forward(reporter.connection().identity, \
                _(u"Join Error. Provided location code (%(loc)s) is wrong.") % {'loc': location_code})
            return True
    
        # check that location is a clinic (not sure about that)
        #if not clinic.type in LocationType.objects.filter(name='Clinic'):
        #    message.forward(reporter.connection().identity, \
        #        _(u"Join Error. You must provide a Clinic code."))
        #    return True

        # set location
        reporter.location = location

        # check role code
        try:
            role  = Role.objects.get(code=role_code)
        except models.ObjectDoesNotExist:
            message.forward(reporter.connection().identity, \
                _(u"Join Error. Provided Role code (%(role)s) is wrong.") % {'role': role_code})
            return True

        reporter.role   = role

        # set account active
        # /!\ we use registered_self as active
        reporter.registered_self  = True

        # save modifications
        reporter.save()

        # inform target
        message.forward(reporter.connection().identity, \
            _("Success. You are now registered as %(role)s at %(loc)s with alias @%(alias)s.") % {'loc': location, 'role': reporter.role, 'alias': reporter.alias})

        #inform admin
        if message.persistant_connection.reporter != reporter:
            message.respond( \
            _("Success. %(reporter)s is now registered as %(role)s at %(loc)s with alias @%(alias)s.") % {'reporter': reporter, 'loc': location, 'role': reporter.role, 'alias': reporter.alias})
        return True

    def respond_to_join(self, message, info):
        """respond to join """
        message.respond(
           _("%(mobile)s registered to @%(username)s " +
              "(%(user_last_name)s, %(user_first_name)s) at %(location)s.") % info)
        
    @keyword(r'confirm (\w+)')
    def confirm_join (self, message, username):
        """confirm that a user is part of the system """
        mobile   = message.peer
        message.respond("youwanna confirm eh?")
        
        try:
            user = User.objects.get(username__iexact=username)
        except User.DoesNotExist:
            self.respond_not_registered(username)
        for provider in Provider.objects.filter(mobile=mobile):
            if provider.user.id == user.id:
                provider.active = True
            else:
                provider.active = False
            provider.save()
        info = provider.get_dictionary()
        self.respond_to_join(message, info)
        log(provider, "confirmed_join")
        return True

    
    def respond_not_registered (self, message, target):
        """user not registered """
        raise HandlerFailed(_("User @%s is not registered.") % target)

    def find_provider (self, target):
        """FIND PROVIDER """
        try:
            if re.match(r'^\d+$', target):
                reporter = Reporter.objects.get(id=target)
            else:
                reporter = Reporter.objects.get(alias__iexact=target)                
            return reporter
        except models.ObjectDoesNotExist:
            # FIXME: try looking up a group
            self.respond_not_registered(message, target)

    @keyword(r'\@(\w+) (.+)')
    @registered

    def direct_message (self, message, target, text):
        """Direct your message to my DOCSTRING"""

        provider = self.find_provider(target)
        try:
            mobile = provider.mobile
        except:
            self.respond_not_registered(message, target)
        sender = message.sender.username
        return message.forward(mobile, "@%s> %s" % (sender, text))
        
    def get_new_patient_format_reminder(self):
        """Expected format for new command, sent as a reminder"""
        return "Format:  new [patient last name] [patient first name] [patient gender m/f] [patient dob ddmmyyyy] [patient guardian] (contact number)"


    # Register a new patient
    @keyword(r'new (\S+) (\S+) ([MF]) ([\d\-]+)( \D+)?( \d+)?( z\d+)?')
    @registered
    def new_case (self, message, last, first, gender, dob,guardian="", contact="", zone=None):
        """Expected format for nnnnew command, sent as a reminder"""
        """Format: <>new [patient last name] [patient first name] gender[m/f] [dob ddmmyy] [guardian] [contact #] [zone - optional]<> 
       Purpose: register a new patient into the system.  Receive patient ID number back"""


        # reporter
        reporter    = message.persistant_connection.reporter
        
        
        dob = re.sub(r'\D', '', dob)
        try:
            dob = time.strptime(dob, "%d%m%y")
        except ValueError:
            try:
                dob = time.strptime(dob, "%d%m%Y")
            except ValueError:
                raise HandlerFailed(_("Couldn't understand date: %s") % dob)
        dob = datetime.date(*dob[:3])
        if guardian:
            guardian = guardian.title()
        # todo: move this to a more generic get_description
        info = {
            "first_name" : first.title(),
            "last_name"  : last.title(),
            "gender"     : gender.upper()[0],
            "dob"        : dob,
            "guardian"   : guardian,
            "mobile"     : contact,
            "reporter"   : reporter,
            "location"       : reporter.location
        }

        ## check to see if the case already exists
        iscase = Case.objects.filter(first_name=info['first_name'], last_name=info['last_name'], reporter=info['reporter'], dob=info['dob'])
        if iscase:
            #message.respond(_(
            #"%(last_name)s, %(first_name)s has already been registered by you.") % info)
            info["PID"] = iscase[0].ref_id
            self.info(iscase[0].id)
            self.info(info)
            message.respond(_(
            "%(last_name)s, %(first_name)s (+%(PID)s) has already been registered by %(reporter)s.") % info)
            # TODO: log this message
            return True
        case = Case(**info)
        case.save()

        info.update({
            "id": case.ref_id,
            "last_name": last.upper(),
            "age": case.age()
        })
        
        message.respond(_(
            "New +%(id)s: %(last_name)s, %(first_name)s %(gender)s/%(age)s " +
            "(%(guardian)s) %(location)s") % info)
        
        log(case, "patient_created")
        return True

    def find_case (self, ref_id):
        """look up a patient id """
        try:
            return Case.objects.get(ref_id=int(ref_id))
        except Case.DoesNotExist:
            raise HandlerFailed(_("Case +%s not found.") % ref_id)

    @keyword(r'cancel \+?(\d+)')
    @authenticated
    def cancel_case (self, message, ref_id):
        """OOOOOOPSIES CANCEL """
        case = self.find_case(ref_id)
        if case.reportmalnutrition_set.count():
            raise HandlerFailed(_(
                "Cannot cancel +%s: case has malnutrition reports.") % ref_id)
                
        if case.reportmalaria_set.count():
            raise HandlerFailed(_(
                "Cannot cancel +%s: case has malaria reports.") % ref_id)
                
        if case.reportdiagnosis_set.count():
            raise HandlerFailed(_(
                "Cannot cancel +%s: case has diagnosis reports.") % ref_id)

        case.delete()
        message.respond(_("Case +%s cancelled.") % ref_id)
        
        
        log(message.sender.provider, "case_cancelled")        
        return True
    
    @keyword(r'inactive \+?(\d+)?(.+)')
    @authenticated
    def inactive_case (self, message, ref_id, reason=""):
        """set case status to inactive. """
        case = self.find_case(ref_id)
        case.set_status(Case.STATUS_INACTIVE)
        case.save()
        info = case.get_dictionary()
        message.respond(_(
            "+%(ref_id)s: %(last_name)s, %(first_name)s %(gender)s/%(months)s " +
            "(%(guardian)s) has been made inactive") % info)
        return True

    def get_transfer_format_reminder(self):
        """Expected format for transfer command, sent as a reminder"""
        return "Format:  transfer [+patient ID] [new person in charge of the patient]"

    @keyword(r'transfer \+?(\d+) (?:to )?\@?(\w+)')
    @registered
    def transfer_case (self, message, ref_id, target):
        """heyExpected format for transfer command, sent as a reminder"""

        reporter    = message.persistant_connection.reporter
        case = self.find_case(ref_id)
        new_provider = self.find_provider(target) 
        case.reporter = new_provider
        case.save()
        info = {
            'username': new_provider.alias,
            'name': new_provider.full_name(),
            'location':new_provider.location
        }
        info["ref_id"] = case.ref_id
        message.respond(_("Case +%(ref_id)s transferred to @%(username)s " +
                         "(%(name)s - %(location)s).") % info)
        
        message.forward(new_provider.connection().identity,
                        _("Case +%s transferred to you from @%s (%s - %s).")%
                        (case.ref_id, reporter.alias, reporter.full_name(), reporter.location))
        
        log(case, "case_transferred")        
        return True
 
    @keyword(r's(?:how)? \+?(\d+)')
    @registered
    def show_case (self, message, ref_id):
        """THIS IS MY DOCSTRING  """
        case = self.find_case(ref_id)
        info = case.get_dictionary()

        message.respond(_(
            "+%(ref_id)s %(status)s %(last_name)s, %(first_name)s "
            "%(gender)s/%(age)s %(guardian)s - %(location)s") % info)        
        
        return True
    
    @keyword(r'n(?:ote)? \+(\d+) (.+)')
    @registered
    def note_case (self, message, ref_id, note):
        """NOTE MY DOCSTRING :(
        """

        reporter    = message.persistant_connection.reporter
        case = self.find_case(ref_id)
        CaseNote(case=case, created_by=reporter, text=note).save()
        message.respond(_("Note added to case +%s.") % ref_id)
        
        log(case, "note_added")        
        return True

    
