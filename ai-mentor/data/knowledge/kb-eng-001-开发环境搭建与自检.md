---
id: kb-eng-001
title: 开发环境搭建与自检
track: engineering, general
tags: 环境, 工具链, 第一天
owner: 研发效能组
review_date: 2027-06-30
source: 工程规范 v2 / 研发效能手册
---

## 标准步骤

1. 安装 Git，安装时务必勾选 **Add to PATH**，装完重新打开终端。
2. 配置身份，否则提交记录会认不出你：

```bash
git config --global user.name "你的名字"
git config --global user.email "你的公司邮箱"
```

3. 创建统一的工作目录 `%USERPROFILE%\work`，所有仓库都克隆到这里。
4. 安装 Node.js LTS（前端/工具链需要）与 Python 3.10+。
5. 跑一遍本工具的「环境自检」，逐项把失败项修掉。

## 分支与提交流程

- 主分支 `main` 受保护，任何人都不能直接推送。
- 开发分支命名：`feat/需求号-简述`、`fix/问题号-简述`。
- 提交信息格式：`类型(范围): 做了什么`，例如 `feat(order): 支持优惠叠加策略`。

## 遇到报错的排查顺序

1. 完整复制报错原文，先搜一遍。
2. 确认依赖版本与团队一致（`node -v`、`python -V`）。
3. 检查是不是路径里有空格或中文。
4. 仍然不通，按「高效求助」的模板带上已试步骤去问。

## 不要做的事

- 不要把仓库放在桌面或下载目录。
- 不要在 `main` 上直接改代码。
- 不要把 `node_modules`、构建产物提交进仓库。
