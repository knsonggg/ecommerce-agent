# 单店小团队部署

本项目支持商家内部试运行。正式接收业务资料前，应验证模型效果、知识准确性和备份恢复；平台收发接口尚未接入。

## 本机开发

```bash
python server.py
```

浏览器打开 http://127.0.0.1:8000 。可使用 `--port 8002` 改端口；若设置了 `APP_ORIGIN`，必须同时更新为浏览器实际访问的地址。服务不会自动打开公网监听。

## HTTPS 反向代理

1. 建立专用系统账户与项目虚拟环境，并限制项目私有数据目录的系统访问权限。
2. 安装固定版本依赖：

```bash
python -m venv .venv
# Windows
.venv\Scripts\python -m pip install -r requirements.txt
# Linux
# .venv/bin/python -m pip install -r requirements.txt
```

3. 在 `.env` 配置实际域名及私有数据库位置。例如：

```dotenv
APP_ORIGIN=https://support.example.com
# 这里填写服务器实际的绝对路径，不要使用示例路径。
# COMMERCE_DB=/srv/commerce-private/commerce.sqlite3
DEEPSEEK_API_KEY=
DEEPSEEK_MODEL=deepseek-flash
```

4. 生产模式禁止网页首次初始化。先在服务器终端创建管理员：

```bash
python manage.py create-admin
```

若本地已创建管理员且迁移了数据库，请保留已有账号，不再初始化。密码不会通过命令行参数传递。

5. 启动生产 WSGI 服务：

```bash
python server.py --production
```

生产模式只在 `127.0.0.1:8000` 监听，由同机 HTTPS 反向代理转发，保留原始 Host。通过进程管理器配置工作目录、自动重启及服务账户；不要以管理员/root 运行。

反向代理需要：终止 TLS；保留实际 Host；限制请求体为 1 MB；上游读取超时至少 150 秒。不要把 `data/` 作为静态文件目录暴露。应用不信任客户端 X-Forwarded-For，反向代理应配置登录入口的 IP 限流，因为应用在该部署方式下看到的是代理 IP。

生产 Cookie 具有 Secure、HttpOnly 和 SameSite=Strict 属性，响应提供 CSP 与 HSTS。生产模式拒绝 HTTP 的 `APP_ORIGIN`。TLS 和服务进程托管由部署环境负责。

## 容量与失败处理

- 当前设计为**单进程、小团队、单店**。会话和工单持久化到 SQLite，WAL 便于读写并行。
- 应用进程内限制同时处理 4 个模型任务；每账号每分钟 20 次生成；同一咨询只允许一个生成任务。
- 登录限制为每个来源 IP 和每个账号每 10 分钟 10 次尝试。该限流在重启后重置，不适合多副本部署；扩容时需迁移到共享限流与 PostgreSQL。
- 模型最多 6 轮、每轮 8 个工具调用，约 120 秒预算，网络调用单次超时最高 40 秒。上游异常不会自动以演示回复冒充模型结果。
- 页面重试会复用当前请求编号，防止重复落库；跨页面恢复时先查看历史记录再决定是否重发。
- 错误返回请求编号；服务日志只记录请求编号和异常类型，不记录密钥或咨询正文。
- 消息和工具结果保存在数据库以便追溯，因此应限制数据库文件与备份的访问。

## 运行维护

每天定时运行 `manage.py backup` 到私有备份位置，并实际演练停机恢复；定时备份由部署环境安排。升级前先备份，首次运行会创建缺少的表和字段。不要用早期版本代码直接操作升级后的数据库。

员工离职时在成员管理中停用账号，已有登录会话会撤销。忘记密码时使用 `manage.py reset-password`，不是直接修改数据库或在聊天中传递密码。

此版本没有对话自动保留期、批量归档、企业 SSO 或独立安全审计。引入真实业务前，应确定数据保留策略及人工处理责任；自动退款、平台改价等写操作不能仅凭模型输出执行。

## 接入平台时的扩展边界

平台连接应放在独立适配器中：验证平台回调签名和时间窗；用平台事件编号去重；把顾客和店铺身份映射到内部会话；订单工具仅获得本店授权数据；回复先遵循明确的人审/自动发送策略。平台令牌只保存在服务器私有配置中。

目前所有聊天入口都面向授权商家成员，不能把现有 `/api/chat` 直接公开给顾客。需要平台名称、接口授权和沙箱后，再实现与验收该部分。

实现依据：[DeepSeek Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/)、[Python sqlite3](https://docs.python.org/3/library/sqlite3.html)、[Waitress](https://docs.pylonsproject.org/projects/waitress/en/stable/)。
