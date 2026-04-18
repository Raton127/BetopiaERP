# Part of Odoo. See LICENSE file for full copyright and licensing details.

import json
import logging
import psycopg2

import odoo.api
import odoo.exceptions
import odoo.modules.registry
from odoo import http
from odoo.exceptions import AccessError
from odoo.http import request
from odoo.service import security
from odoo.tools.misc import hmac
from odoo.tools.translate import _, LazyTranslate
from .utils import (
    ensure_db,
    _get_login_redirect_url,
    is_user_internal,
)

_lt = LazyTranslate(__name__)
_logger = logging.getLogger(__name__)

SIGN_UP_REQUEST_PARAMS = {'db', 'login', 'debug', 'token', 'message', 'error', 'scope', 'mode',
                          'redirect', 'redirect_hostname', 'email', 'name', 'partner_id',
                          'password', 'confirm_password', 'city', 'country_id', 'lang', 'signup_email'}
LOGIN_SUCCESSFUL_PARAMS = set()
CREDENTIAL_PARAMS = ['login', 'password', 'type']
APP_ROOT = '/BetopiaERP'
APP_WEB_ROOT = APP_ROOT + '/web'
LEGACY_APP_ROOT = '/odoo'


class Home(http.Controller):

    @http.route('/', type='http', auth="none")
    def index(self, s_action=None, db=None, **kw):
        if request.db and request.session.uid and not is_user_internal(request.session.uid):
            return request.redirect_query(APP_WEB_ROOT + '/login_successful', query=request.params)
        return request.redirect_query('/web', query=request.params)

    def _web_client_readonly(self, rule, args):
        return False

    @http.route(['/web', APP_ROOT, APP_ROOT + '/<path:subpath>', LEGACY_APP_ROOT, LEGACY_APP_ROOT + '/<path:subpath>', '/scoped_app/<path:subpath>'], type='http', auth="none", readonly=_web_client_readonly)
    def web_client(self, s_action=None, **kw):
        subpath = kw.get('subpath')
        current_path = request.httprequest.path or ''
        if current_path == LEGACY_APP_ROOT or current_path.startswith(LEGACY_APP_ROOT + '/'):
            suffix = current_path[len(LEGACY_APP_ROOT):] or ''
            query = ('?' + request.httprequest.query_string.decode()) if request.httprequest.query_string else ''
            return request.redirect(APP_ROOT + suffix + query, 301)

        ensure_db()
        if not request.session.uid:
            return request.redirect_query(APP_WEB_ROOT + '/login', query={'redirect': request.httprequest.full_path}, code=303)
        if kw.get('redirect'):
            return request.redirect(kw.get('redirect'), 303)
        if not security.check_session(request.session, request.env, request):
            raise http.SessionExpiredException("Session expired")
        if not is_user_internal(request.session.uid):
            return request.redirect(APP_WEB_ROOT + '/login_successful', 303)

        request.session.touch()
        request.update_env(user=request.session.uid)
        try:
            if request.env.user:
                request.env.user._on_webclient_bootstrap()
            context = request.env['ir.http'].webclient_rendering_context()
            hmac_payload = request.env.user._session_token_get_values()
            session_info = context.get("session_info")
            session_info['browser_cache_secret'] = hmac(request.env(su=True), "browser_cache_key", hmac_payload)

            response = request.render('web.webclient_bootstrap', qcontext=context)
            response.headers['X-Frame-Options'] = 'DENY'
            response.headers['Cache-Control'] = 'no-store'
            return response
        except AccessError:
            return request.redirect(APP_WEB_ROOT + '/login?error=access')

    @http.route(['/web/webclient/load_menus', APP_WEB_ROOT + '/webclient/load_menus'], type='http', auth='user', methods=['GET'], readonly=True)
    def web_load_menus(self, lang=None):
        if lang:
            request.update_context(lang=lang)
        menus = request.env["ir.ui.menu"].load_web_menus(request.session.debug)
        body = json.dumps(menus)
        response = request.make_response(body, [
            ('Content-Type', 'application/json'),
            ('Cache-Control', 'public, max-age=' + str(http.STATIC_CACHE_LONG)),
        ])
        return response

    def _login_redirect(self, uid, redirect=None):
        return _get_login_redirect_url(uid, redirect)

    @http.route(['/web/login', APP_WEB_ROOT + '/login'], type='http', auth='none', readonly=False, list_as_website_content=_lt("Login"))
    def web_login(self, redirect=None, **kw):
        ensure_db()
        request.params['login_success'] = False
        if request.httprequest.method == 'GET' and redirect and request.session.uid:
            return request.redirect(redirect)
        if request.env.uid is None:
            if request.session.uid is None:
                request.env["ir.http"]._auth_method_public()
            else:
                request.update_env(user=request.session.uid)
        values = {k: v for k, v in request.params.items() if k in SIGN_UP_REQUEST_PARAMS}
        try:
            values['databases'] = http.db_list()
        except odoo.exceptions.AccessDenied:
            values['databases'] = None
        if request.httprequest.method == 'POST':
            try:
                credential = {key: value for key, value in request.params.items() if key in CREDENTIAL_PARAMS and value}
                credential.setdefault('type', 'password')
                if request.env['res.users']._should_captcha_login(credential):
                    request.env['ir.http']._verify_request_recaptcha_token('login')
                auth_info = request.session.authenticate(request.env, credential)
                request.params['login_success'] = True
                return request.redirect(self._login_redirect(auth_info['uid'], redirect=redirect))
            except odoo.exceptions.AccessDenied as e:
                if e.args == odoo.exceptions.AccessDenied().args:
                    values['error'] = _("Wrong login/password")
                else:
                    values['error'] = e.args[0]
        else:
            if 'error' in request.params and request.params.get('error') == 'access':
                values['error'] = _('Only employees can access this database. Please contact the administrator.')
        if 'login' not in values and request.session.get('auth_login'):
            values['login'] = request.session.get('auth_login')
        if not odoo.tools.config['list_db']:
            values['disable_database_manager'] = True
        response = request.render('web.login', values)
        response.headers['Cache-Control'] = 'no-cache'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['Content-Security-Policy'] = "frame-ancestors 'self'"
        return response

    @http.route(['/web/login_successful', APP_WEB_ROOT + '/login_successful'], type='http', auth='user', website=True, sitemap=False)
    def login_successful_external_user(self, **kwargs):
        valid_values = {k: v for k, v in kwargs.items() if k in LOGIN_SUCCESSFUL_PARAMS}
        return request.render('web.login_successful', valid_values)

    @http.route(['/web/become', APP_WEB_ROOT + '/become'], type='http', auth='user', sitemap=False, readonly=True)
    def switch_to_admin(self):
        uid = request.env.user.id
        if request.env.user._is_system():
            uid = request.session.uid = odoo.SUPERUSER_ID
            request.env.registry.clear_cache()
            request.session.session_token = security.compute_session_token(request.session, request.env)
        return request.redirect(self._login_redirect(uid))

    @http.route(['/web/health', APP_WEB_ROOT + '/health'], type='http', auth='none', save_session=False)
    def health(self, db_server_status=False):
        health_info = {'status': 'pass'}
        status = 200
        if db_server_status:
            try:
                odoo.sql_db.db_connect('postgres').cursor().close()
                health_info['db_server_status'] = True
            except psycopg2.Error:
                health_info['db_server_status'] = False
                health_info['status'] = 'fail'
                status = 500
        data = json.dumps(health_info)
        headers = [('Content-Type', 'application/json'), ('Cache-Control', 'no-store')]
        return request.make_response(data, headers, status=status)

    @http.route(['/robots.txt'], type='http', auth="none")
    def robots(self, **kwargs):
        allowed_routes = self._get_allowed_robots_routes()
        robots_content = ["User-agent: *", "Disallow: /"]
        robots_content.extend(f"Allow: {route}" for route in allowed_routes)
        return request.make_response("\n".join(robots_content), [('Content-Type', 'text/plain')])


    def _get_allowed_robots_routes(self):
        return []