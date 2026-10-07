"""P2 合并工作台契约用例（C4 干预复查等）共享 fixture。

复用 tests/v1/conftest.py 的合成样本（v1_seed）与 TestClient fixture——
通过通配导入把 fixture 重新导出给本目录用例；isolated_module_schema
（模块级 drop/create_all）依然逐模块生效，两个测试模块互不串数据。
"""
