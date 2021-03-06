# -*- coding: utf-8 -*-
# Copyright 2017 Matt Taylor
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
{
    "name": "azi_account",
    "version": "10.0.1.1.0",
    "summary": "AZI account Customizations",
    "category": "Accounting",
    "author": "Matt Taylor",
    "license": "AGPL-3",
    "website": "http://www.asphaltzipper.com",
    'description': """
AZI Specialized Customizations to account
=========================================

* Show product on journal entry form view
* Show product on journal item tree view
* Add fields on reconciliation form
    * Analytic Tags
    * Product
* Add Receipt on File field to journal item
* Add menu item for account types
* Reformat check
    """,
    "depends": ['account'],
    'data': [
        'views/account.xml',
        'views/account_view_changes.xml',
        'views/account_move_line_views.xml',
        'report/report_invoice.xml',
    ],
    "installable": True,
    "auto_install": False,
}
