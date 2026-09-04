# 项目台账 (Project Hub)

本地轻量项目管理系统：自动扫描项目目录生成卡片墙，支持状态/客户/截止日期标记、全文搜索（含 Office/PDF 正文）、文件浏览与预览。

## 功能
- 📁 自动扫描项目目录（容器目录智能下钻，资料堆不拆卡）
- 🏷️ 项目类型（客户项目/开发项目/归档）自动识别 + 筛选
- 📅 截止日期倒计时提醒
- 🔍 全文搜索：文件名 + 内容（FTS5，支持 docx/pptx/xlsx/pdf 正文提取）
- 📄 文件预览（文本/图片/PDF/Office正文提取），一键用系统程序打开
- 🕐 后台扫描线程 + 快照持久化，界面零阻塞

## 架构
Python3 + Flask + SQLite(FTS5) + 原生 JS，无前端构建依赖。

## 快速开始
```bash
pip install flask
python3 build_index.py        # 建全文索引（约10秒）
python3 app.py --daemon       # 启动(双fork守护)
# 浏览器打开 http://localhost:8765
```

配置修改：`app.py` 顶部 `ROOT`（扫描根目录）、`PORT`。
