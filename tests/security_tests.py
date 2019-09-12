# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
import inspect
import prison
import unittest

from superset import app, appbuilder, db, security_manager
from superset.connectors.sqla.models import SqlaTable
from superset.models.core import Database, Slice
from .base_tests import SupersetTestCase


def get_perm_tuples(role_name):
    perm_set = set()
    for perm in security_manager.find_role(role_name).permissions:
        perm_set.add((perm.permission.name, perm.view_menu.name))
    return perm_set


SCHEMA_ACCESS_ROLE = "schema_access_role"


class RolePermissionTests(SupersetTestCase):
    """Testing export role permissions."""

    @classmethod
    def setUpClass(cls):
        session = db.session
        security_manager.add_role(SCHEMA_ACCESS_ROLE)
        session.commit()

        ds = (
            db.session.query(SqlaTable)
            .filter_by(table_name="wb_health_population")
            .first()
        )
        ds.schema = "temp_schema"
        ds.schema_perm = ds.get_schema_perm()

        ds_slices = (
            session.query(Slice)
            .filter_by(datasource_type="table")
            .filter_by(datasource_id=ds.id)
            .all()
        )
        for s in ds_slices:
            s.schema_perm = ds.schema_perm

        security_manager.add_permission_view_menu("schema_access", ds.schema_perm)
        schema_perm_view = security_manager.find_permission_view_menu(
            "schema_access", ds.schema_perm
        )
        security_manager.add_permission_role(
            security_manager.find_role(SCHEMA_ACCESS_ROLE), schema_perm_view
        )
        gamma_user = security_manager.find_user(username="gamma")
        gamma_user.roles.append(security_manager.find_role(SCHEMA_ACCESS_ROLE))
        session.commit()

    @classmethod
    def tearDownClass(cls):
        session = db.session
        ds = (
            session.query(SqlaTable)
            .filter_by(table_name="wb_health_population")
            .first()
        )
        ds.schema = None
        ds_slices = (
            session.query(Slice)
            .filter_by(datasource_type="table")
            .filter_by(datasource_id=ds.id)
            .all()
        )
        for s in ds_slices:
            s.schema_perm = None
        session.delete(security_manager.find_role(SCHEMA_ACCESS_ROLE))
        session.commit()

    def test_gamma_user_schema_access_to_dashboards(self):
        self.login(username="gamma")
        data = str(self.client.get("dashboard/list/").data)
        assert "/superset/dashboard/world_health/" in data
        assert "/superset/dashboard/births/" not in data

    def test_gamma_user_schema_access_to_tables(self):
        self.login(username="gamma")
        data = str(self.client.get("tablemodelview/list/").data)
        assert "wb_health_population" in data
        assert "birth_names" not in data

    def test_gamma_user_schema_access_to_charts(self):
        self.login(username="gamma")
        data = str(self.client.get("chart/list/").data)
        assert (
            "Life Expectancy VS Rural %" in data
        )  # wb_health_population slice, has access
        assert "Parallel Coordinates" in data  # wb_health_population slice, has access
        assert "Girl Name Cloud" not in data  # birth_names slice, no access

    def test_sqllab_gamma_user_schema_access_to_sqllab(self):
        session = db.session

        example_db = session.query(Database).filter_by(database_name="examples").one()
        example_db.expose_in_sqllab = True
        session.commit()

        OLD_FLASK_GET_SQL_DBS_REQUEST = (
            "databaseasync/api/read?_flt_0_expose_in_sqllab=1&"
            "_oc_DatabaseAsync=database_name&_od_DatabaseAsync=asc"
        )
        self.login(username="gamma")
        databases_json = self.client.get(OLD_FLASK_GET_SQL_DBS_REQUEST).json
        assert databases_json["count"] == 1

        arguments = {
            "keys": ["none"],
            "filters": [{"col": "expose_in_sqllab", "opr": "eq", "value": True}],
            "order_columns": "database_name",
            "order_direction": "asc",
            "page": 0,
            "page_size": -1,
        }
        NEW_FLASK_GET_SQL_DBS_REQUEST = f"/api/v1/database/?q={prison.dumps(arguments)}"
        self.login(username="gamma")
        databases_json = self.client.get(NEW_FLASK_GET_SQL_DBS_REQUEST).json
        assert databases_json["count"] == 1
        self.logout()

    def assert_can_read(self, view_menu, permissions_set):
        self.assertIn(("can_show", view_menu), permissions_set)
        self.assertIn(("can_list", view_menu), permissions_set)

    def assert_can_write(self, view_menu, permissions_set):
        self.assertIn(("can_add", view_menu), permissions_set)
        self.assertIn(("can_download", view_menu), permissions_set)
        self.assertIn(("can_delete", view_menu), permissions_set)
        self.assertIn(("can_edit", view_menu), permissions_set)

    def assert_cannot_write(self, view_menu, permissions_set):
        self.assertNotIn(("can_add", view_menu), permissions_set)
        self.assertNotIn(("can_download", view_menu), permissions_set)
        self.assertNotIn(("can_delete", view_menu), permissions_set)
        self.assertNotIn(("can_edit", view_menu), permissions_set)
        self.assertNotIn(("can_save", view_menu), permissions_set)

    def assert_can_all(self, view_menu, permissions_set):
        self.assert_can_read(view_menu, permissions_set)
        self.assert_can_write(view_menu, permissions_set)

    def assert_cannot_gamma(self, perm_set):
        self.assert_cannot_write("DruidColumnInlineView", perm_set)

    def assert_can_gamma(self, perm_set):
        self.assert_can_read("DatabaseAsync", perm_set)
        self.assert_can_read("TableModelView", perm_set)

        # make sure that user can create slices and dashboards
        self.assert_can_all("SliceModelView", perm_set)
        self.assert_can_all("DashboardModelView", perm_set)

        self.assertIn(("can_add_slices", "Superset"), perm_set)
        self.assertIn(("can_copy_dash", "Superset"), perm_set)
        self.assertIn(("can_created_dashboards", "Superset"), perm_set)
        self.assertIn(("can_created_slices", "Superset"), perm_set)
        self.assertIn(("can_csv", "Superset"), perm_set)
        self.assertIn(("can_dashboard", "Superset"), perm_set)
        self.assertIn(("can_explore", "Superset"), perm_set)
        self.assertIn(("can_explore_json", "Superset"), perm_set)
        self.assertIn(("can_fave_dashboards", "Superset"), perm_set)
        self.assertIn(("can_fave_slices", "Superset"), perm_set)
        self.assertIn(("can_save_dash", "Superset"), perm_set)
        self.assertIn(("can_slice", "Superset"), perm_set)
        self.assertIn(("can_explore", "Superset"), perm_set)
        self.assertIn(("can_explore_json", "Superset"), perm_set)
        self.assertIn(("can_userinfo", "UserDBModelView"), perm_set)

    def assert_can_alpha(self, perm_set):
        self.assert_can_all("SqlMetricInlineView", perm_set)
        self.assert_can_all("TableColumnInlineView", perm_set)
        self.assert_can_all("TableModelView", perm_set)
        self.assert_can_all("DruidColumnInlineView", perm_set)
        self.assert_can_all("DruidDatasourceModelView", perm_set)
        self.assert_can_all("DruidMetricInlineView", perm_set)

        self.assertIn(("all_datasource_access", "all_datasource_access"), perm_set)
        self.assertIn(("muldelete", "DruidDatasourceModelView"), perm_set)

    def assert_cannot_alpha(self, perm_set):
        if app.config.get("ENABLE_ACCESS_REQUEST"):
            self.assert_cannot_write("AccessRequestsModelView", perm_set)
            self.assert_can_all("AccessRequestsModelView", perm_set)
        self.assert_cannot_write("Queries", perm_set)
        self.assert_cannot_write("RoleModelView", perm_set)
        self.assert_cannot_write("UserDBModelView", perm_set)

    def assert_can_admin(self, perm_set):
        self.assert_can_read("DatabaseAsync", perm_set)
        self.assert_can_all("DatabaseView", perm_set)
        self.assert_can_all("DruidClusterModelView", perm_set)
        self.assert_can_all("RoleModelView", perm_set)
        self.assert_can_all("UserDBModelView", perm_set)

        self.assertIn(("all_database_access", "all_database_access"), perm_set)
        self.assertIn(("can_override_role_permissions", "Superset"), perm_set)
        self.assertIn(("can_sync_druid_source", "Superset"), perm_set)
        self.assertIn(("can_override_role_permissions", "Superset"), perm_set)
        self.assertIn(("can_approve", "Superset"), perm_set)

    def test_is_admin_only(self):
        self.assertFalse(
            security_manager._is_admin_only(
                security_manager.find_permission_view_menu("can_show", "TableModelView")
            )
        )
        self.assertFalse(
            security_manager._is_admin_only(
                security_manager.find_permission_view_menu(
                    "all_datasource_access", "all_datasource_access"
                )
            )
        )

        self.assertTrue(
            security_manager._is_admin_only(
                security_manager.find_permission_view_menu("can_delete", "DatabaseView")
            )
        )
        if app.config.get("ENABLE_ACCESS_REQUEST"):
            self.assertTrue(
                security_manager._is_admin_only(
                    security_manager.find_permission_view_menu(
                        "can_show", "AccessRequestsModelView"
                    )
                )
            )
        self.assertTrue(
            security_manager._is_admin_only(
                security_manager.find_permission_view_menu(
                    "can_edit", "UserDBModelView"
                )
            )
        )
        self.assertTrue(
            security_manager._is_admin_only(
                security_manager.find_permission_view_menu("can_approve", "Superset")
            )
        )

    @unittest.skipUnless(
        SupersetTestCase.is_module_installed("pydruid"), "pydruid not installed"
    )
    def test_is_alpha_only(self):
        self.assertFalse(
            security_manager._is_alpha_only(
                security_manager.find_permission_view_menu("can_show", "TableModelView")
            )
        )

        self.assertTrue(
            security_manager._is_alpha_only(
                security_manager.find_permission_view_menu(
                    "muldelete", "TableModelView"
                )
            )
        )
        self.assertTrue(
            security_manager._is_alpha_only(
                security_manager.find_permission_view_menu(
                    "all_datasource_access", "all_datasource_access"
                )
            )
        )
        self.assertTrue(
            security_manager._is_alpha_only(
                security_manager.find_permission_view_menu(
                    "can_edit", "SqlMetricInlineView"
                )
            )
        )
        self.assertTrue(
            security_manager._is_alpha_only(
                security_manager.find_permission_view_menu(
                    "can_delete", "DruidMetricInlineView"
                )
            )
        )
        self.assertTrue(
            security_manager._is_alpha_only(
                security_manager.find_permission_view_menu(
                    "all_database_access", "all_database_access"
                )
            )
        )

    def test_is_gamma_pvm(self):
        self.assertTrue(
            security_manager._is_gamma_pvm(
                security_manager.find_permission_view_menu("can_show", "TableModelView")
            )
        )

    def test_gamma_permissions_basic(self):
        self.assert_can_gamma(get_perm_tuples("Gamma"))
        self.assert_cannot_gamma(get_perm_tuples("Gamma"))
        self.assert_cannot_alpha(get_perm_tuples("Alpha"))

    @unittest.skipUnless(
        SupersetTestCase.is_module_installed("pydruid"), "pydruid not installed"
    )
    def test_alpha_permissions(self):
        self.assert_can_gamma(get_perm_tuples("Alpha"))
        self.assert_can_alpha(get_perm_tuples("Alpha"))
        self.assert_cannot_alpha(get_perm_tuples("Alpha"))

    @unittest.skipUnless(
        SupersetTestCase.is_module_installed("pydruid"), "pydruid not installed"
    )
    def test_admin_permissions(self):
        self.assert_can_gamma(get_perm_tuples("Admin"))
        self.assert_can_alpha(get_perm_tuples("Admin"))
        self.assert_can_admin(get_perm_tuples("Admin"))

    def test_sql_lab_permissions(self):
        sql_lab_set = get_perm_tuples("sql_lab")
        self.assertIn(("can_sql_json", "Superset"), sql_lab_set)
        self.assertIn(("can_csv", "Superset"), sql_lab_set)
        self.assertIn(("can_search_queries", "Superset"), sql_lab_set)

        self.assert_cannot_gamma(sql_lab_set)
        self.assert_cannot_alpha(sql_lab_set)

    def test_granter_permissions(self):
        granter_set = get_perm_tuples("granter")
        self.assertIn(("can_override_role_permissions", "Superset"), granter_set)
        self.assertIn(("can_approve", "Superset"), granter_set)

        self.assert_cannot_gamma(granter_set)
        self.assert_cannot_alpha(granter_set)

    def test_gamma_permissions(self):
        def assert_can_read(view_menu):
            self.assertIn(("can_show", view_menu), gamma_perm_set)
            self.assertIn(("can_list", view_menu), gamma_perm_set)

        def assert_can_write(view_menu):
            self.assertIn(("can_add", view_menu), gamma_perm_set)
            self.assertIn(("can_download", view_menu), gamma_perm_set)
            self.assertIn(("can_delete", view_menu), gamma_perm_set)
            self.assertIn(("can_edit", view_menu), gamma_perm_set)

        def assert_cannot_write(view_menu):
            self.assertNotIn(("can_add", view_menu), gamma_perm_set)
            self.assertNotIn(("can_download", view_menu), gamma_perm_set)
            self.assertNotIn(("can_delete", view_menu), gamma_perm_set)
            self.assertNotIn(("can_edit", view_menu), gamma_perm_set)
            self.assertNotIn(("can_save", view_menu), gamma_perm_set)

        def assert_can_all(view_menu):
            assert_can_read(view_menu)
            assert_can_write(view_menu)

        gamma_perm_set = set()
        for perm in security_manager.find_role("Gamma").permissions:
            gamma_perm_set.add((perm.permission.name, perm.view_menu.name))

        # check read only perms
        assert_can_read("TableModelView")
        assert_cannot_write("DruidColumnInlineView")

        # make sure that user can create slices and dashboards
        assert_can_all("SliceModelView")
        assert_can_all("DashboardModelView")

        self.assertIn(("can_add_slices", "Superset"), gamma_perm_set)
        self.assertIn(("can_copy_dash", "Superset"), gamma_perm_set)
        self.assertIn(("can_created_dashboards", "Superset"), gamma_perm_set)
        self.assertIn(("can_created_slices", "Superset"), gamma_perm_set)
        self.assertIn(("can_csv", "Superset"), gamma_perm_set)
        self.assertIn(("can_dashboard", "Superset"), gamma_perm_set)
        self.assertIn(("can_explore", "Superset"), gamma_perm_set)
        self.assertIn(("can_explore_json", "Superset"), gamma_perm_set)
        self.assertIn(("can_fave_dashboards", "Superset"), gamma_perm_set)
        self.assertIn(("can_fave_slices", "Superset"), gamma_perm_set)
        self.assertIn(("can_save_dash", "Superset"), gamma_perm_set)
        self.assertIn(("can_slice", "Superset"), gamma_perm_set)
        self.assertIn(("can_userinfo", "UserDBModelView"), gamma_perm_set)

    def test_views_are_secured(self):
        """Preventing the addition of unsecured views without has_access decorator"""
        # These FAB views are secured in their body as opposed to by decorators
        method_whitelist = ("action", "action_post")
        # List of redirect & other benign views
        views_whitelist = [
            ["MyIndexView", "index"],
            ["UtilView", "back"],
            ["LocaleView", "index"],
            ["AuthDBView", "login"],
            ["AuthDBView", "logout"],
            ["R", "index"],
            ["Superset", "log"],
            ["Superset", "theme"],
            ["Superset", "welcome"],
            ["SecurityApi", "login"],
            ["SecurityApi", "refresh"],
        ]
        unsecured_views = []
        for view_class in appbuilder.baseviews:
            class_name = view_class.__class__.__name__
            for name, value in inspect.getmembers(
                view_class, predicate=inspect.ismethod
            ):
                if (
                    name not in method_whitelist
                    and [class_name, name] not in views_whitelist
                    and hasattr(value, "_urls")
                    and not hasattr(value, "_permission_name")
                ):
                    unsecured_views.append((class_name, name))
        if unsecured_views:
            view_str = "\n".join([str(v) for v in unsecured_views])
            raise Exception(f"Some views are not secured:\n{view_str}")
