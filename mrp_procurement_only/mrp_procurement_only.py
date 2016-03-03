# -*- coding: utf-8 -*-
# See __openerp__.py file for full copyright and licensing details.

from datetime import datetime
from openerp import models, fields, SUPERUSER_ID
from dateutil.relativedelta import relativedelta
from openerp.tools import DEFAULT_SERVER_DATE_FORMAT
import openerp
import logging
_logger = logging.getLogger(__name__)


class stock_warehouse_orderpoint(models.Model):
    _inherit = 'stock.warehouse.orderpoint'

    # override stock/stock:subtract_procurements_from_orderpoints
    def subtract_procurements_from_orderpoints(self, cr, uid, orderpoint_ids, context=None):
        '''This function returns quantity of product that needs to be deducted from the orderpoint computed quantity because there's already a procurement created with aim to fulfill it.
        We are also considering procurement-only demand for the product and subtracting it from the returned quantity. This method could return a negative quantity, assuming that there is more demand than supply.
        '''

        # only consider procurements within current plan step (context['to_date'])
        cr.execute("""select op.id, p.id, p.product_uom, p.product_qty, pt.uom_id, sm.product_qty, p.origin from procurement_order as p left join stock_move as sm ON sm.procurement_id = p.id,
                                    stock_warehouse_orderpoint op, product_product pp, product_template pt
                                WHERE p.orderpoint_id = op.id AND p.state not in ('done', 'cancel') AND (sm.state IS NULL OR sm.state not in ('draft'))
                                AND pp.id = p.product_id AND pp.product_tmpl_id = pt.id
                                AND op.id IN %s
                                AND p.date_planned <= %s
                                ORDER BY op.id, p.id
                   """, (tuple(orderpoint_ids), context.get('to_date', datetime.max),))
        results = cr.fetchall()
        current_proc = False
        current_op = False
        uom_obj = self.pool.get("product.uom")
        op_qty = 0
        res = dict.fromkeys(orderpoint_ids, 0.0)
        for move_result in results:
            op = move_result[0]
            if current_op != op:
                if current_op:
                    res[current_op] = op_qty
                current_op = op
                op_qty = 0
            proc = move_result[1]
            if proc != current_proc:
                if 'OUT/' in move_result[6]:
                    # subtract outbound procurements for production
                    op_qty -= uom_obj._compute_qty(cr, uid, move_result[2], move_result[3], move_result[4], round=False)
                else:
                    op_qty += uom_obj._compute_qty(cr, uid, move_result[2], move_result[3], move_result[4], round=False)
                current_proc = proc
            if move_result[5]: #If a move is associated (is move qty)
                op_qty -= move_result[5]
        if current_op:
            res[current_op] = op_qty
        return res


class procurement_order(models.Model):
    _inherit = 'procurement.order'
    _order = 'priority desc, date_start, date_planned, id asc'

    date_start = fields.Datetime('Start Date', required=False, select=True)

    # stock/procurement,procurement/procurement
    def run_scheduler(self, cr, uid, use_new_cursor=False, company_id=False, context=None):
        '''
        Call the scheduler in order to check the running procurements (this REPLACES run_scheduler to eliminate
        automatic execution of run for confirmed procurements), to check the minimum stock rules and the availability
        of moves. This function is intended to be run for all the companies at the same time, so we run functions as
        SUPERUSER to avoid intercompanies and access rights issues.
        @param self: The object pointer
        @param cr: The current row, from the database cursor,
        @param uid: The current user ID for security checks
        @param ids: List of selected IDs
        @param use_new_cursor: if set, use a dedicated cursor and auto-commit after processing each procurement.
            This is appropriate for batch jobs only.
        @param context: A standard dictionary for contextual values
        @return:  Dictionary of values
        '''
        if context is None:
            context = {}
        try:
            if use_new_cursor:
                cr = openerp.registry(cr.dbname).cursor()

            # Check if running procurements are done
            offset = 0
            dom = [('state', '=', 'running')]
            if company_id:
                dom += [('company_id', '=', company_id)]
            prev_ids = []
            while True:
                ids = self.search(cr, SUPERUSER_ID, dom, offset=offset, context=context)
                if not ids or prev_ids == ids:
                    break
                else:
                    prev_ids = ids
                self.check(cr, SUPERUSER_ID, ids, autocommit=use_new_cursor, context=context)
                if use_new_cursor:
                    cr.commit()

            move_obj = self.pool.get('stock.move')

            #Minimum stock rules
            self._procure_orderpoint_confirm(cr, SUPERUSER_ID, use_new_cursor=use_new_cursor, company_id=company_id, context=context)

            #Search all confirmed stock_moves and try to assign them
            confirmed_ids = move_obj.search(cr, uid, [('state', '=', 'confirmed')], limit=None, order='priority desc, date_expected asc', context=context)
            for x in xrange(0, len(confirmed_ids), 100):
                move_obj.action_assign(cr, uid, confirmed_ids[x:x + 100], context=context)
                if use_new_cursor:
                    cr.commit()

            if use_new_cursor:
                cr.commit()
        finally:
            if use_new_cursor:
                try:
                    cr.close()
                except Exception:
                    pass
        return {}

    def run(self, cr, uid, ids, autocommit=False, context=None):
        super(procurement_order, self).run(cr, uid, ids, autocommit, context=context)

    # procurement/procurement:run_scheduler
    def run_procurement(self, cr, uid, ids, use_new_cursor=False, context=None):
        context = context or {}
        try:
            if use_new_cursor:
                cr = openerp.registry(cr.dbname).cursor()

            # Run selected procurements
            dom = [('id', 'in', ids), ('state', '=', 'confirmed')]
            prev_ids = []
            while True:
                ids = self.search(cr, SUPERUSER_ID, dom, context=context)
                if not ids or prev_ids == ids:
                    break
                else:
                    prev_ids = ids
                self.run(cr, SUPERUSER_ID, ids, autocommit=use_new_cursor, context=context)
                if use_new_cursor:
                    cr.commit()

            # Check if selected running procurements are done
            offset = 0
            dom = [('id', 'in', ids), ('state', '=', 'running')]
            prev_ids = []
            while True:
                ids = self.search(cr, SUPERUSER_ID, dom, offset=offset, context=context)
                if not ids or prev_ids == ids:
                    break
                else:
                    prev_ids = ids
                self.check(cr, SUPERUSER_ID, ids, autocommit=use_new_cursor, context=context)
                if use_new_cursor:
                    cr.commit()
        finally:
            if use_new_cursor:
                try:
                    cr.close()
                except Exception:
                    pass
        return {}

    def _get_procurement_date_start(self, cr, uid, orderpoint, to_date, context=None):
        days = 0.0
        # make addition of lead_days an optional setting
        days += orderpoint.lead_days or 0.0
        product = orderpoint.product_id
        for route in product.route_ids:
            if route.pull_ids:
                for rule in route.pull_ids:
                    if rule.action == 'buy':
                        days += product._select_seller(product).delay or 0.0
                        days += product.product_tmpl_id.company_id.po_lead
                    if rule.action == 'manufacture':
                        days += product.produce_delay or 0.0
                        days += product.product_tmpl_id.company_id.manufacturing_lead
        date_start = datetime.combine(datetime.strptime(to_date, DEFAULT_SERVER_DATE_FORMAT) - relativedelta(days=days), datetime.min.time())
        return date_start.strftime(DEFAULT_SERVER_DATE_FORMAT)

    # stock/procurement,mrp_time_bucket/mrp_time_bucket
    def _prepare_orderpoint_procurement(self, cr, uid, orderpoint, product_qty, context=None):
        res = super(procurement_order, self)._prepare_orderpoint_procurement(cr, uid, orderpoint, product_qty, context=context)
        res['date_start'] = self._get_procurement_date_start(cr, uid, orderpoint, context['bucket_date'], context=context)
        _logger.info(" IN res: %s", res)
        return res

    def _prepare_outbound_procurement(self, cr, uid, orderpoint, product, product_qty, product_uom, context=None):
        # get orderpoint_id of current product where location/warehouse same as parent?
        #   mrp/procurement.py:_prepare_mo_vals
        #       'location_src_id': procurement.rule_id.location_src_id.id or procurement.location_id.id,
        #   stock/procurement.py:_find_suitable_rule
        #   location is based on procurement rule_id or procurement location_id, these are not set yet
        #   if rule_id is not set before run, it will try to _find_suitable_rule
        # it seems the location_id for an OB proc should be some form of production (dest), but no ops would exist for such
        #   perhaps should reconsider:
        #    'location_id': self.pool.get('ir.model.data').xmlid_to_res_id(cr, uid, 'stock.location_production'),
        all_parent_location_ids = self._find_parent_locations(cr, uid, orderpoint, context=context)
        location_domain = [('location_id', 'in', all_parent_location_ids)]
        child_orderpoint_id = self.pool.get('stock.warehouse.orderpoint').search(cr, uid, [
            ('product_id', '=', product.id),
            ('warehouse_id', '=', orderpoint.warehouse_id.id),
            ] + location_domain, limit=1)
        #loc_obj = self.pool.get('stock.location')
        #production_location_id = False
        #production_locations = loc_obj.search(cr, uid, [('usage', '=', 'production')])
        #for location in production_locations:
        #    warehouse_id = loc_obj.get_warehouse(cr, uid, loc_obj.browse(cr, uid, location), context=context)
        #    if warehouse_id == orderpoint.warehouse_id.id:
        #        production_location_id = location
        #        continue

        #product_route_ids = [x.id for x in product.route_ids + product.categ_id.total_route_ids]
        #child_rule_id = self.pool.get('procurement.rule').search(cr, uid, location_domain + [('route_id', 'in', product_route_ids)], order='route_sequence, sequence', context=context)
        # dirty hack to pretend orderpoint is procurement in order to use _search_suitable_rule
        orderpoint.route_ids = []
        child_rule_id = self._search_suitable_rule(cr, uid, orderpoint, location_domain, context=context)
        del orderpoint.route_ids
        child_location_id = child_rule_id and self.pool.get('procurement.rule').browse(cr, uid, child_rule_id)[0].location_src_id or False
        res = {
            'name': product.product_tmpl_id.name,
            'date_planned': context['child_to_date'],
            'product_id': product.id,
            'product_qty': product_qty,
            'company_id': product.product_tmpl_id.company_id.id,
            'product_uom': product_uom,
            #'location_id': orderpoint.location_id.id,
            #'location_id': child_rule_id and self.pool.get('procurement.rule').browse(cr, uid, child_rule_id)[0].location_src_id.id or False,
            'location_id': child_location_id and child_location_id.id or product.property_stock_production.id,
            'origin': 'OUT/PROC/' + '%%0%sd' % 5 % context['parent_proc_id'],
            #'warehouse_id': loc_obj.get_warehouse(cr, uid, loc_obj.browse(cr, uid, location_id), context=context),
            'warehouse_id': orderpoint.warehouse_id.id,
            'date_start': context['child_to_date'],
            'orderpoint_id': child_orderpoint_id and child_orderpoint_id[0] or False,
        }
        _logger.info("OUT res: %s", res)
        return res

    # override mrp_time_bucket/mrp_time_bucket
    def _process_procurement(self, cr, uid, ids, context=None):
        # REPLACE _process_procurement to eliminate automatic execution of run for confirmed procurements
        #self.check(cr, uid, ids)
        pass

    # mrp_time_bucket/mrp_time_bucket
    def _plan_orderpoint_procurement(self, cr, uid, op, qty_rounded, context=None):
        context = context or {}
        proc_id = super(procurement_order, self)._plan_orderpoint_procurement(cr, uid, op, qty_rounded, context=context)
        proc_ids = []
        proc_ids.append(proc_id)
        if proc_id:
            bom_obj = self.pool.get('mrp.bom')
            bom_id = bom_obj._bom_find(cr, uid, product_id=op.product_id.id, context=context)
            if bom_id:
                uom_obj = self.pool.get('product.uom')
                procurement_obj = self.pool.get('procurement.order')
                proc_point = procurement_obj.browse(cr, uid, proc_id)
                bom_point = bom_obj.browse(cr, uid, bom_id)
                # get components and workcenter_lines from BoM structure
                factor = uom_obj._compute_qty(cr, uid, proc_point.product_uom.id, proc_point.product_qty, bom_point.product_uom.id)
                # product_lines, workcenter_lines (False)
                res = bom_obj._bom_explode(cr, uid, bom_point, proc_point.product_id, factor / bom_point.product_qty, context=context)
                # product_lines
                results = res[0]
                context['child_to_date'] = proc_point.date_start
                context['parent_proc_id'] = proc_id
                # process procurements for results
                for product in results:
                    product_obj = self.pool.get('product.product')
                    product_point = product_obj.browse(cr, uid, product['product_id'])
                    proc_id += procurement_obj.create(cr, uid,
                                                    self._prepare_outbound_procurement(cr, uid, op, product_point, product['product_qty'], product['product_uom'], context=context),
                                                    context=context)
                    proc_id and proc_ids.append(proc_id) or False
                context.pop('child_to_date')
                context.pop('parent_proc_id')
        return proc_ids

    # stock/procurement,mrp_time_bucket/mrp_time_bucket
    def _procure_orderpoint_confirm(self, cr, uid, use_new_cursor=False, company_id = False, context=None):
        # delete all procurements matching
        #   created by engine
        #   in confirmed state
        #   not linked to any RFQ or MO
        procurement_obj = self.pool.get('procurement.order')
        dom = [('state', '=', 'confirmed'),
               ('purchase_line_id', '=', False),
               ('production_id', '=', False),
               '|', ('origin', 'like', 'OP/'), ('origin', 'like', 'OUT/')]
        proc_ids = procurement_obj.search(cr, uid, dom) or []
        if proc_ids:
            procurement_obj.cancel(cr, SUPERUSER_ID, proc_ids, context=context)
            procurement_obj.unlink(cr, SUPERUSER_ID, proc_ids, context=context)
            if use_new_cursor:
                cr.commit()

        super(procurement_order, self)._procure_orderpoint_confirm(cr, uid, use_new_cursor, company_id, context=context)
