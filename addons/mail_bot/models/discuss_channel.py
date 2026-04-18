# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, models, _


class DiscussChannel(models.Model):
    _inherit = 'discuss.channel'

    def execute_command_help(self, **kwargs):
        super().execute_command_help(**kwargs)

    def _message_post_after_hook(self, message, msg_vals):
        return super()._message_post_after_hook(message, msg_vals)
