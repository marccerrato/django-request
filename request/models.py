from socket import gethostbyaddr

from django.db import models
from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.utils.translation import ugettext_lazy as _

from request.managers import RequestManager
from request.utils import HTTP_STATUS_CODES, browsers, engines, get_client_ip

import datetime
from django.utils.timezone import utc

from request import settings


class Request(models.Model):
    # Response infomation
    response = models.SmallIntegerField(_('response'), choices=HTTP_STATUS_CODES, default=200)

    # Request infomation
    method = models.CharField(_('method'), default='GET', max_length=7)
    path = models.CharField(_('path'), max_length=255)
    time = models.DateTimeField(_('time'))

    is_secure = models.BooleanField(_('is secure'), default=False)
    is_ajax = models.BooleanField(_('is ajax'), default=False, help_text=_('Wheather this request was used via javascript.'))

    # User infomation
    ip = models.IPAddressField(_('ip address'))
    user = models.ForeignKey(User, blank=True, null=True, verbose_name=_('user'))
    referer = models.URLField(_('referer'), max_length=255, blank=True, null=True)
    user_agent = models.CharField(_('user agent'), max_length=255, blank=True, null=True)
    language = models.CharField(_('language'), max_length=255, blank=True, null=True)
    session_key = models.CharField(_('session key'), max_length=40, blank=True, null=True)

    objects = RequestManager()

    def save(self, *args, **kwargs):
        """On save, update timestamps"""
        if not self.id and not self.time:
            self.time = datetime.datetime.utcnow().replace(tzinfo=utc)

    class Meta:
        verbose_name = _('request')
        verbose_name_plural = _('requests')
        ordering = ('-time',)

    def __unicode__(self):
        return u'[%s] %s %s %s' % (self.time, self.method, self.path, self.response)

    def get_user(self):
        return User.objects.get(pk=self.user_id)

    def from_http_request(self, request, response=None):
        # Request infomation
        self.time = datetime.datetime.utcnow().replace(tzinfo=utc)
        self.method = request.method
        self.path = request.path

        self.is_secure = request.is_secure()
        self.is_ajax = request.is_ajax()

        # User infomation
        self.ip = get_client_ip(request)
        self.referer = request.META.get('HTTP_REFERER', '')[:255]
        self.user_agent = request.META.get('HTTP_USER_AGENT', '')[:255]
        self.language = request.META.get('HTTP_ACCEPT_LANGUAGE', '')[:255]
        if hasattr(request, 'session'):
            self.session_key = request.session.session_key

        if getattr(request, 'user', False):
            if request.user.is_authenticated():
                self.user = request.user

        if response:
            self.response = response.status_code

            if (response.status_code == 301) or (response.status_code == 302):
                self.redirect = response['Location']

    @classmethod
    def create_from_http_request(cls, request, response=None, commit=True):
        r = cls()
        r.from_http_request(request, response)

        # save the request
        if commit:
            if settings.REQUEST_BUFFER_SIZE == 0:
                r.save()
            else:
                settings.REQUEST_BUFFER.append(r)
                if len(settings.REQUEST_BUFFER) > settings.REQUEST_BUFFER_SIZE:
                    try:
                        Request.objects.bulk_create(settings.REQUEST_BUFFER)
                    except:
                        pass
                    settings.REQUEST_BUFFER = []

    @staticmethod
    def get_open_sessions_from_users(user_ids):
        """
        Return a SQL cursor with the list of newest request grouped
        by session for a given list of user ids
        """
        now = datetime.datetime.utcnow().replace(tzinfo=utc)

        query = """SELECT max(r.id) as id \
                FROM request_request r, django_session s \
                WHERE r.user_id IN %s and \
                r.session_key = s.session_key and \
                s.expire_date >= %s \
                GROUP BY s.session_key \
                ORDER BY r.time DESC"""

        rqs = Request.objects.raw(query, [user_ids, now])

        return rqs

    #@property
    def browser(self):
        if not self.user_agent:
            return

        if not hasattr(self, '_browser'):
            self._browser = browsers.resolve(self.user_agent)
        return self._browser[0]
    browser = property(browser)

    #@property
    def keywords(self):
        if not self.referer:
            return

        if not hasattr(self, '_keywords'):
            self._keywords = engines.resolve(self.referer)
        if self._keywords:
            return ' '.join(self._keywords[1]['keywords'].split('+'))
    keywords = property(keywords)

    #@property
    def hostname(self):
        try:
            return gethostbyaddr(self.ip)[0]
        except Exception:  # socket.gaierror, socket.herror, etc
            return self.ip
    hostname = property(hostname)

    def save(self, force_insert=False, force_update=False, using=None, update_fields=None):
        if not settings.REQUEST_LOG_IP:
            self.ip = settings.REQUEST_IP_DUMMY
        elif settings.REQUEST_ANONYMOUS_IP:
            parts = self.ip.split('.')[0:-1]
            parts.append('1')
            self.ip='.'.join(parts)
        if not settings.REQUEST_LOG_USER:
            self.user = None

        return models.Model.save(self, force_insert, force_update, using, update_fields)
