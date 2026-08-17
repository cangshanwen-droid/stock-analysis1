"""stock-api 最小冒烟测试（无 pytest 依赖，用 unittest）——修复测试覆盖缺口"""
import unittest
import os
import sys
import py_compile

class TestStockApiSmoke(unittest.TestCase):
    """stock_mini.py 语法 + 关键函数存在性冒烟（补测试覆盖缺口）"""

    def test_01_py_compile(self):
        """语法编译通过"""
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "api", "stock_mini.py")
        self.assertTrue(os.path.exists(path), "stock_mini.py 不存在")
        py_compile.compile(path, doraise=True)

    def test_02_key_symbols(self):
        """关键端点/函数存在（防回归：交易端点、鉴权、资金桥）"""
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "api", "stock_mini.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        for sym in [
            '@app.post("/orders")', '@app.get("/portfolio")',
            '@app.get("/fund-accounts")', '@app.post("/fund-accounts")',
            '@app.get("/managed-stock-accounts")',
            '@app.get("/admin/accounts")', '@app.get("/market")',
            '@app.get("/admin/control/overview")',
            '@app.post("/admin/market/close")',
            '@app.post("/admin/market/open")',
            '@app.post("/admin/market/previous-round")',
            '@app.post("/admin/market/reset-round1")',
            '@app.get("/admin/audit-logs")',
            '@app.post("/auth/login")',
            'def _require_auth', 'def _require_self', 'def _require_admin',
            'def _require_trader',
            'def _stock_fund_call', 'def _stock_fund_rollback',
            'from datetime import datetime',
            'X-Internal-Key',  # 资金桥内部密钥
            '"Bearer "',  # Bearer 鉴权
            'CREATE TABLE IF NOT EXISTS market_state',
            'round INTEGER DEFAULT 1',
        ]:
            self.assertIn(sym, src, f"缺少符号: {sym}")

    def test_03_no_pep604(self):
        """线上代码零 PEP 604（Python 3.8 兼容纪律）"""
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "api", "stock_mini.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        import re
        pep604 = re.findall(r":\s*(int|str|float|bool)\s*\|\s*(None|int|str|float)", src)
        self.assertEqual(pep604, [], f"发现 PEP 604 语法（Python 3.8 不支持）: {pep604}")

    def test_04_user_level_stock_balance_is_consistent(self):
        """Current product model uses one independent stock balance per user."""
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "api", "stock_mini.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        self.assertIn('UPDATE users SET balance = balance - ?', src)
        self.assertIn('UPDATE users SET balance = balance + ?', src)
        self.assertIn('cash = float(user["balance"] or 0)', src)

    def test_05_representative_is_api_readonly(self):
        """Representative read-only mode must be enforced by the API."""
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "api", "stock_mini.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        trader_block = src[src.index("def _require_trader"):src.index("def _require_self")]
        self.assertIn('session.get("role") not in ("admin", "operator")', trader_block)
        self.assertIn('raise HTTPException(403', trader_block)

    def test_06_kline_is_round_based(self):
        """K 线必须按比赛轮次聚合，禁止退回自然日时间轴。"""
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "api", "stock_mini.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        kline = src[src.index('def stock_kline'):src.index('@app.get("/stocks/{symbol}/order-book")')]
        self.assertIn('for order_round in range(1, current_round + 1)', kline)
        self.assertIn('"time": f"round-{order_round}"', kline)
        self.assertIn('ORDER BY round ASC, id ASC', kline)
        self.assertNotIn('by_day', kline)


if __name__ == "__main__":
    unittest.main(verbosity=2)
