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
            '@app.get("/admin/accounts")', '@app.get("/market")',
            '@app.post("/auth/login")',
            'def _require_auth', 'def _require_self', 'def _require_admin',
            'def _stock_fund_call', 'def _stock_fund_rollback',
            'X-Internal-Key',  # 资金桥内部密钥
            '"Bearer "',  # Bearer 鉴权
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

    def test_04_no_local_balance_leftover(self):
        """资金显示路径零本地余额残留（跨库改造后）"""
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "api", "stock_mini.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        leftovers = [ln for ln in src.splitlines()
                     if ('user["balance"]' in ln or 'u["balance"]' in ln)
                     and "_stock_fund_call" not in ln]
        self.assertEqual(leftovers, [], f"本地余额读取残留: {leftovers[:3]}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
