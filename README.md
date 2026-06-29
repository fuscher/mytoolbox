# MyToolbox - 私人工具箱

一个基于 Python 的 Windows 应用程序管理工具，提供安装包管理和已安装应用卸载功能。

## 功能特性

### 📦 安装包管理
- 查看所有安装包列表，支持按分类筛选
- 批量导入安装包文件（支持 .exe、.msi、.msu）
- 分类管理安装包（新增、编辑、删除分类）
- 图标自动提取与缓存（支持从 .exe 和 .msi 文件提取）
- 一键安装功能（自动请求 UAC 管理员权限）
- 批量操作（批量分类、批量删除）

### 🗑 应用管理
- 扫描系统已安装应用（支持 32/64 位应用）
- 支持标准卸载流程
- 卸载后自动扫描残留文件
- 打开应用安装目录

## 技术栈

- Python 3.9+
- Tkinter (GUI)
- ctypes (Windows API 调用)
- JSON (数据存储)

**可选依赖（提升图标提取稳定性）：**
- `pywin32` - 更稳定的图标提取
- `psutil` - 卸载时终止相关进程

## 快速开始

### 运行程序

```bash
python main.py
```

### CLI 命令

```bash
# 扫描并列出所有工具
python main.py scan
# 或
python main.py list
```

## 项目结构

```
MyToolbox/
├── core/              # 核心模块
│   ├── app_manager.py     # 应用管理（扫描/卸载）
│   ├── icon_extractor.py  # 图标提取（支持 ctypes/pywin32/subprocess）
│   ├── index_manager.py   # 索引管理
│   ├── installer.py       # 安装器（支持 UAC 提升）
│   ├── junk_scanner.py    # 残留扫描
│   ├── models.py          # 数据模型
│   ├── scanner.py         # 工具扫描
│   └── state.py           # 状态管理
├── gui/               # GUI 模块
│   ├── app.py             # 主窗口
│   ├── install_tab.py     # 安装包管理 Tab
│   ├── uninstaller_tab.py # 应用管理 Tab
│   └── dialogs.py         # 对话框
├── resources/         # 资源文件
│   └── default_icon.png
├── tools/             # 安装包目录
│   ├── _index.json        # 索引文件
│   └── _categories.json   # 分类配置
├── config.json        # 配置文件
└── main.py            # 入口文件
```

## 配置说明

`config.json` 配置项：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| tools_dir | 安装包目录 | tools |
| default_icon | 默认图标路径 | resources/default_icon.png |
| install_timeout_minutes | 安装超时时间（分钟） | 10 |
| registry_poll_interval_seconds | 注册表轮询间隔（秒） | 2 |
| theme | 主题 | default |

## 使用说明

### 添加安装包

1. 点击「导入安装包」按钮
2. 选择 .exe、.msi 或 .msu 文件
3. 程序自动提取工具名称和版本号
4. 选择分类（可选）
5. 点击确定添加

### 分类管理

1. 点击「分类管理」按钮
2. 可查看、新增、编辑、删除分类

### 批量操作

1. 勾选「批量选择」复选框
2. 选择多个安装包
3. 进行批量分类或删除

### 安装程序

1. 点击安装包卡片上的「安装」按钮
2. 系统会弹出 UAC 权限确认对话框
3. 确认后启动安装程序

## 注意事项

- 安装程序时需要管理员权限，系统会自动请求 UAC 提升
- 图标提取采用三级容错机制：pywin32 → ctypes → subprocess，确保稳定性
- 安装包文件存储在 `tools/` 目录下，每个工具对应一个子目录

## 许可证

本项目基于 [Apache License 2.0](LICENSE) 发布。

```
SPDX-License-Identifier: Apache-2.0
```