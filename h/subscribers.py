# -*- coding: utf-8 -*-
from pyramid.events import subscriber
from pyramid.renderers import get_renderer
from pyramid.settings import asbool

from h import events


@subscriber(events.BeforeRender)
def add_renderer_globals(event):
    request = event['request']

    # Set the base url to use in the <base> tag
    if hasattr(request, 'root'):
        event['base_url'] = request.resource_url(request.root, 'app')

    # Set the blocks property to refer to the block helpers template
    event['blocks'] = get_renderer('templates/blocks.pt').implementation()


@subscriber(events.NewResponse)
def set_csrf_cookie(event):
    request = event.request
    response = event.response
    session = request.session
    token = session.get_csrf_token()
    if request.cookies.get('XSRF-TOKEN') != token:
        response.set_cookie('XSRF-TOKEN', token)


@subscriber(events.NewRegistrationEvent)
@subscriber(events.RegistrationActivatedEvent)
def registration(event):
    request = event.request
    settings = request.registry.settings
    autologin = asbool(settings.get('horus.autologin', False))

    if isinstance(event, events.RegistrationActivatedEvent) or autologin:
        request.user = event.user


def includeme(config):
    config.scan(__name__)
