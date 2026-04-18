# Part of Odoo. See LICENSE file for full copyright and licensing details.

import collections
import logging

import babel.messages.pofile
import werkzeug
import werkzeug.exceptions
import werkzeug.utils
import werkzeug.wrappers
import werkzeug.wsgi
from werkzeug.urls import iri_to_uri

from odoo.tools.translate import JAVASCRIPT_TRANSLATION_COMMENT
from odoo.tools.misc import file_open
from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)
APP_ROOT = '/BetopiaERP'
APP_WEB_ROOT = APP_ROOT + '/web'


def clean_action(action, env):
    action_type = action.setdefault('type', 'ir.actions.act_window_close')
    if action_type == 'ir.actions.act_window' and not action.get('views'):
        generate_views(action)

    readable_fields = env[action['type']]._get_readable_fields()
    action_type_fields = env[action['type']]._fields.keys()

    cleaned_action = {
        field: value
        for field, value in action.items()
        if field in readable_fields or field not in action_type_fields
    }

    action_name = action.get('name') or action
    custom_properties = action.keys() - readable_fields - action_type_fields
    if custom_properties:
        _logger.warning("Action %r contains custom properties %s. Passing them via the `params` or `context` properties is recommended instead", action_name, ', '.join(map(repr, custom_properties)))

    return cleaned_action


def ensure_db(redirect='/web/database/selector', db=None):
    if db is None:
        db = request.params.get('db') and request.params.get('db').strip()
    if db and db not in http.db_filter([db]):
        db = None
    if db and not request.session.db:
        r = request.httprequest
        url_redirect = werkzeug.urls.url_parse(r.base_url)
        if r.query_string:
            query_string = iri_to_uri(r.query_string.decode())
            url_redirect = url_redirect.replace(query=query_string)
        request.session.db = db
        werkzeug.exceptions.abort(request.redirect(url_redirect.to_url(), 302))
    if not db and request.session.db and http.db_filter([request.session.db]):
        db = request.session.db
    if not db:
        all_dbs = http.db_list(force=True)
        if len(all_dbs) == 1:
            db = all_dbs[0]
    if not db:
        werkzeug.exceptions.abort(request.redirect(redirect, 303))
    if db != request.session.db:
        request.session = http.root.session_store.new()
        request.session.update(http.get_default_session(), db=db)
        request.session.context['lang'] = request.default_lang()
        werkzeug.exceptions.abort(request.redirect(request.httprequest.url, 302))


def generate_views(action):
    view_id = action.get('view_id') or False
    if isinstance(view_id, (list, tuple)):
        view_id = view_id[0]
    view_modes = action['view_mode'].split(',')
    if len(view_modes) > 1:
        if view_id:
            raise ValueError('Non-db action dictionaries should provide either multiple view modes or a single view mode and an optional view id.\n\n Got view modes %r and view id %r for action %r' % (
                view_modes, view_id, action))
        action['views'] = [(False, mode) for mode in view_modes]
        return
    action['views'] = [(view_id, view_modes[0])]


def get_action(env, path_part):
    Actions = env['ir.actions.actions']
    if path_part.startswith('action-'):
        someid = path_part.removeprefix('action-')
        if someid.isdigit():
            action = Actions.sudo().browse(int(someid)).exists()
        elif '.' in someid:
            action = env.ref(someid, False)
            if not action or not action._name.startswith('ir.actions'):
                action = Actions
        else:
            action = Actions
    elif path_part.startswith('m-') or '.' in path_part:
        model = path_part.removeprefix('m-')
        if model in env and not env[model]._abstract:
            action = env['ir.actions.act_window'].sudo().search([('res_model', '=', model)], limit=1)
            if not action:
                action = env['ir.actions.act_window'].new(env[model].get_formview_action())
        else:
            action = Actions
    else:
        action = Actions.sudo().search([('path', '=', path_part)])
    if action and action._name == 'ir.actions.actions':
        action_type = action.read(['type'])[0]['type']
        action = env[action_type].browse(action.id)
    return action


def get_action_triples(env, path, *, start_pos=0):
    parts = collections.deque(path.strip('/').split('/'))
    active_id = None
    record_id = None
    while parts:
        if not parts:
            e = "expected action at word {} but found nothing"
            raise ValueError(e.format(path.count('/') + start_pos))
        action_name = parts.popleft()
        action = get_action(env, action_name)
        if not action:
            e = f"expected action at word {{}} but found {action_name!r}"
            raise ValueError(e.format(path.count('/') - len(parts) + start_pos))
        record_id = None
        if parts:
            if parts[0] == 'new':
                parts.popleft()
                record_id = None
            elif parts[0].isdigit():
                record_id = int(parts.popleft())
        yield (active_id, action, record_id)
        if len(parts) > 1 and parts[0].isdigit():
            active_id = int(parts.popleft())
        elif record_id:
            active_id = record_id


def _get_login_redirect_url(uid, redirect=None):
    if request.session.uid:
        return redirect or (APP_ROOT if is_user_internal(request.session.uid) else APP_WEB_ROOT + '/login_successful')
    url = request.env(user=uid)['res.users'].browse(uid)._mfa_url()
    if not redirect:
        return url
    parsed = werkzeug.urls.url_parse(url)
    qs = parsed.decode_query()
    qs['redirect'] = redirect
    return parsed.replace(query=werkzeug.urls.url_encode(qs)).to_url()


def is_user_internal(uid):
    return request.env['res.users'].browse(uid)._is_internal()


def _local_web_translations(trans_file):
    messages = []
    try:
        with file_open(trans_file, filter_ext=('.po')) as t_file:
            po = babel.messages.pofile.read_po(t_file)
    except Exception:
        return
    for message in po:
        if not message.id or not message.string or message.string == message.id:
            continue
        comments = [c for c in message.auto_comments if c.startswith(JAVASCRIPT_TRANSLATION_COMMENT)]
        if comments:
            messages.append((message.id, message.string))
    return messages
