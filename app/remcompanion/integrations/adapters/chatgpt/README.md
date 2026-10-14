# ChatGPT / Codex App

没有把网页或 GUI 生命周期伪装为任务生命周期。此适配是明确标记的实验 fallback：注册具体产物任务，指定一个完成 marker；只有你的工作流程真的写出 marker 后才提醒。也可在支持运行本机命令的工作流程里使用 task-config 的 started/completed/failed argv。

不要用 ChatGPT 桌面进程退出、浏览器关闭、字符消失推断任务完成；不要抓取私有登录/历史数据库。Linux 只保证标准库通知和文件监工方式，不保证 ChatGPT 桌面存在或支持。

导出不安装到网页/软件。UI取消监工只是取消观察，不停止宿主模型。来源：[官方 ChatGPT](https://openai.com/chatgpt/)。尚未核实原生任务事件接口，manifest 明确 experimental-marker。
