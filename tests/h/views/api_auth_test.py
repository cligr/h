# -*- coding: utf-8 -*-

from __future__ import unicode_literals

from h._compat import url_quote
import datetime

import mock
import pytest
from pyramid import httpexceptions

from h.services.auth_token import auth_token_service_factory
from h.services.oauth import oauth_service_factory
from h.services.user import user_service_factory
from h.exceptions import OAuthTokenError
from h.util.datetime import utc_iso8601
from h.views import api_auth as views


@pytest.mark.usefixtures('routes', 'user_service', 'oauth_service')
class TestOAuthAuthorizeController(object):
    def test_get_raises_if_client_id_invalid(self, auth_ctrl, pyramid_request,
                                             oauth_service):

        oauth_service.get_authclient_by_id.return_value = None

        with pytest.raises(httpexceptions.HTTPBadRequest) as exc:
            auth_ctrl.get()

        assert 'Unknown client ID' in exc.value.message

    def test_get_raises_if_client_authority_incorrect(self, auth_ctrl, pyramid_request,
                                                      oauth_service):

        auth_client = mock.Mock(authority='publisher.org')

        oauth_service.get_authclient_by_id.return_value = auth_client

        with pytest.raises(httpexceptions.HTTPBadRequest) as exc:
            auth_ctrl.get()

        assert 'not allowed to authorize' in exc.value.message

    def test_get_verifies_response_mode(self, auth_ctrl, pyramid_request):
        pyramid_request.GET['response_mode'] = 'invalid_mode'

        with pytest.raises(httpexceptions.HTTPBadRequest) as exc:
            auth_ctrl.get()

        assert 'Unsupported response mode' in exc.value.message

    def test_get_verifies_response_type(self, auth_ctrl, pyramid_request):
        pyramid_request.GET['response_type'] = 'invalid_type'

        with pytest.raises(httpexceptions.HTTPBadRequest) as exc:
            auth_ctrl.get()

        assert 'Unsupported response type' in exc.value.message

    def test_get_redirects_if_user_not_logged_in(self, auth_ctrl,
                                                 pyramid_config, pyramid_request):
        pyramid_config.testing_securitypolicy(None)
        pyramid_request.url = 'http://example.com/auth?client_id=bar'

        with pytest.raises(httpexceptions.HTTPFound) as exc:
            auth_ctrl.get()

        assert exc.value.location == 'http://example.com/login?next={}'.format(
                                       url_quote(pyramid_request.url, safe=''))

    def test_get_returns_expected_context(self, auth_ctrl, user_service):
        ctx = auth_ctrl.get()

        assert ctx == {'username': user_service.fetch.return_value.username,
                       'client_name': 'Hypothesis',
                       'client_id': 'valid_id',
                       'response_type': 'code',
                       'response_mode': 'web_message',
                       'state': 'a_random_string'}

    def test_post_returns_auth_code(self, post_auth_ctrl, oauth_service):
        ctx = post_auth_ctrl.post()
        assert ctx['code'] == oauth_service.create_grant_token.return_value

    def test_post_sets_origin(self, post_auth_ctrl):
        ctx = post_auth_ctrl.post()
        assert ctx['origin'] == 'http://example.com'

    def test_post_returns_state(self, post_auth_ctrl, pyramid_request):
        ctx = post_auth_ctrl.post()
        assert ctx['state'] == 'a_random_string'

    @pytest.fixture
    def auth_ctrl(self, pyramid_config, pyramid_request):
        """
        Configure a valid request for `OAuthAuthorizeController.get`.
        """
        pyramid_config.testing_securitypolicy('acct:fred@example.org')

        pyramid_request.GET['client_id'] = 'valid_id'
        pyramid_request.GET['response_mode'] = 'web_message'
        pyramid_request.GET['response_type'] = 'code'
        pyramid_request.GET['state'] = 'a_random_string'

        return views.OAuthAuthorizeController(pyramid_request)

    @pytest.fixture
    def post_auth_ctrl(self, pyramid_config, pyramid_request):
        """
        Configure a valid request for `OAuthAuthorizeController.post`.
        """
        pyramid_config.testing_securitypolicy('acct:fred@example.org')

        pyramid_request.POST['client_id'] = 'valid_id'
        pyramid_request.POST['response_mode'] = 'web_message'
        pyramid_request.POST['response_type'] = 'code'
        pyramid_request.POST['state'] = 'a_random_string'

        return views.OAuthAuthorizeController(pyramid_request)

    @pytest.fixture
    def oauth_service(self, pyramid_config, pyramid_request):
        svc = mock.Mock(spec_set=oauth_service_factory(None, pyramid_request))

        svc.get_authclient_by_id.return_value = mock.Mock(authority='example.com')

        pyramid_config.register_service(svc, name='oauth')
        return svc

    @pytest.fixture
    def user_service(self, pyramid_config, pyramid_request):
        svc = mock.Mock(spec_set=user_service_factory(None, pyramid_request))
        pyramid_config.register_service(svc, name='user')
        return svc

    @pytest.fixture
    def routes(self, pyramid_config):
        pyramid_config.add_route('login', '/login')


@pytest.mark.usefixtures('user_service', 'oauth_service')
class TestAccessToken(object):
    def test_it_verifies_the_token(self, pyramid_request, oauth_service):
        pyramid_request.POST = {'assertion': 'the-assertion', 'grant_type': 'the-grant-type'}

        views.access_token(pyramid_request)

        oauth_service.verify_token_request.assert_called_once_with(
            pyramid_request.POST
        )

    def test_it_creates_a_token(self, pyramid_request, oauth_service):
        views.access_token(pyramid_request)

        oauth_service.create_token.assert_called_once_with(
            mock.sentinel.user, mock.sentinel.authclient)

    def test_it_returns_an_oauth_compliant_response(self, pyramid_request, token):
        response = views.access_token(pyramid_request)

        assert response['access_token'] == token.value
        assert response['token_type'] == 'bearer'

    def test_it_returns_expires_in_if_the_token_expires(self, factories, pyramid_request, oauth_service):
        token = factories.Token(
            expires=datetime.datetime.utcnow() + datetime.timedelta(hours=1))
        oauth_service.create_token.return_value = token

        assert 'expires_in' in views.access_token(pyramid_request)

    def test_it_does_not_return_expires_in_if_the_token_does_not_expire(self, pyramid_request):
        assert 'expires_in' not in views.access_token(pyramid_request)

    def test_it_returns_the_refresh_token_if_the_token_has_one(self, pyramid_request, token):
        token.refresh_token = 'test_refresh_token'

        assert views.access_token(pyramid_request)['refresh_token'] == token.refresh_token

    def test_it_does_not_returns_the_refresh_token_if_the_token_does_not_have_one(self, pyramid_request):
        assert 'refresh_token' not in views.access_token(pyramid_request)

    @pytest.fixture
    def oauth_service(self, pyramid_config, pyramid_request, token):
        svc = mock.Mock(spec_set=oauth_service_factory(None, pyramid_request))
        svc.verify_token_request.return_value = (mock.sentinel.user, mock.sentinel.authclient)
        svc.create_token.return_value = token
        pyramid_config.register_service(svc, name='oauth')
        return svc

    @pytest.fixture
    def token(self, factories):
        return factories.Token()

    @pytest.fixture
    def user_service(self, pyramid_config, pyramid_request):
        svc = mock.Mock(spec_set=user_service_factory(None, pyramid_request))
        pyramid_config.register_service(svc, name='user')
        return svc


class TestDebugToken(object):
    def test_it_raises_error_when_token_is_missing(self, pyramid_request):
        pyramid_request.auth_token = None

        with pytest.raises(OAuthTokenError) as exc:
            views.debug_token(pyramid_request)

        assert exc.value.type == 'missing_token'
        assert 'Bearer token is missing' in exc.value.message

    def test_it_raises_error_when_token_is_empty(self, pyramid_request):
        pyramid_request.auth_token = ''

        with pytest.raises(OAuthTokenError) as exc:
            views.debug_token(pyramid_request)

        assert exc.value.type == 'missing_token'
        assert 'Bearer token is missing' in exc.value.message

    def test_it_validates_token(self, pyramid_request, token_service):
        pyramid_request.auth_token = 'the-access-token'

        views.debug_token(pyramid_request)

        token_service.validate.assert_called_once_with('the-access-token')

    def test_it_raises_error_when_token_is_invalid(self, pyramid_request, token_service):
        pyramid_request.auth_token = 'the-token'
        token_service.validate.return_value = None

        with pytest.raises(OAuthTokenError) as exc:
            views.debug_token(pyramid_request)

        assert exc.value.type == 'missing_token'
        assert 'Bearer token does not exist or is expired' in exc.value.message

    def test_returns_debug_data_for_oauth_token(self, pyramid_request, token_service, oauth_token):
        pyramid_request.auth_token = oauth_token.value
        token_service.fetch.return_value = oauth_token

        result = views.debug_token(pyramid_request)

        assert result == {'userid': oauth_token.userid,
                          'client': {'id': oauth_token.authclient.id,
                                     'name': oauth_token.authclient.name},
                          'issued_at': utc_iso8601(oauth_token.created),
                          'expires_at': utc_iso8601(oauth_token.expires),
                          'expired': oauth_token.expired}

    def test_returns_debug_data_for_developer_token(self, pyramid_request, token_service, developer_token):
        pyramid_request.auth_token = developer_token.value
        token_service.fetch.return_value = developer_token

        result = views.debug_token(pyramid_request)

        assert result == {'userid': developer_token.userid,
                          'issued_at': utc_iso8601(developer_token.created),
                          'expires_at': None,
                          'expired': False}

    @pytest.fixture
    def token_service(self, pyramid_config, pyramid_request):
        svc = mock.Mock(spec_set=auth_token_service_factory(None, pyramid_request))
        pyramid_config.register_service(svc, name='auth_token')
        return svc

    @pytest.fixture
    def oauth_token(self, factories):
        authclient = factories.AuthClient(name='Example Client')
        expires = datetime.datetime.utcnow() + datetime.timedelta(minutes=10)
        return factories.Token(authclient=authclient, expires=expires)

    @pytest.fixture
    def developer_token(self, factories):
        return factories.Token()


class TestAPITokenError(object):
    def test_it_sets_the_response_status_code(self, pyramid_request):
        context = OAuthTokenError('the error message', 'error_type', status_code=403)
        views.api_token_error(context, pyramid_request)
        assert pyramid_request.response.status_code == 403

    def test_it_returns_the_error(self, pyramid_request):
        context = OAuthTokenError('', 'error_type')
        result = views.api_token_error(context, pyramid_request)
        assert result['error'] == 'error_type'

    def test_it_returns_error_description(self, pyramid_request):
        context = OAuthTokenError('error description', 'error_type')
        result = views.api_token_error(context, pyramid_request)
        assert result['error_description'] == 'error description'

    def test_it_skips_description_when_missing(self, pyramid_request):
        context = OAuthTokenError(None, 'invalid_request')
        result = views.api_token_error(context, pyramid_request)
        assert 'error_description' not in result

    def test_it_skips_description_when_empty(self, pyramid_request):
        context = OAuthTokenError('', 'invalid_request')
        result = views.api_token_error(context, pyramid_request)
        assert 'error_description' not in result
