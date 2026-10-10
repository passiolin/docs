# Markdown 文档站

一个**零构建、零依赖部署**的静态 Markdown 查看器：把两个文件放进你的 Markdown 根目录，就得到一个带层级目录、标题导航、全文搜索和暗色模式的文档站。

## 快速开始

### 方式一：nginx 直接托管（推荐，零脚本）

把 `index.html`、`assets/` 和文章目录（示例中为 `docs/`）放进站点根目录即可，不需要任何生成步骤：

```nginx
server {
    listen 80;
    root /var/www/blog;              # index.html 所在目录

    location /docs/ {
        autoindex on;                # 让查看器可以列出目录（nginx 默认关闭）
    }
}
```

`CONFIG.DOC_ROOT` 保持 `'docs'`。文章增删后**刷新页面就是最新目录**，无需重新生成任何东西。查看器同时兼容 nginx / Apache 的目录列表格式。

> 反向代理场景同理：在代理文档目录的 `location` 里加 `autoindex on;`。
> 如果不想开 autoindex，也可以用方式二生成一份 `manifest.json` 放在站点根目录，优先级更高。

### 方式二：其他静态托管（Vercel / GitHub Pages 等）

这些平台无法开目录列表，用附带脚本生成一次目录索引一起上传：

```bash
python3 build-manifest.py     # 生成 manifest.json（可选，仅这一步用到 python）
```

以后增删文档重新执行一次。不想用 python 的话，任何能产出同格式 `manifest.json` 的办法都行。

## 功能

| 功能 | 说明 |
| --- | --- |
| 层级目录树 | 按文件夹真实层级生成，README 置顶，支持折叠 |
| 标题导航 | 从 H2–H4 提取右侧大纲，滚动自动高亮，点击跳转 |
| 全文搜索 | 跨文件搜索标题和正文，显示摘录（快捷键 `/`） |
| 代码高亮 | 常用语言语法高亮 + 一键复制（鼠标悬停代码块） |
| 其他文件 | xml / json / yaml / properties / conf / sh / 源码等文本文件直接展示并按类型高亮；图片直接预览；无法预览的二进制提供打开与下载 |
| Mermaid 图表 | ` ```mermaid ` 代码块自动渲染成图，跟随明暗主题重新着色，按需加载不拖慢普通页面 |
| 明暗主题 | 跟随系统，可手动切换并记忆 |
| 站内跳转 | 文档里的 `[链接](./xxx.md)` 直接跳转到对应页面 |
| 状态记忆 | 记住上次读到的文档；面包屑、上一篇/下一篇 |
| 响应式 | 侧栏可一键收起 / 展开（记忆状态），窄屏下收进抽屉；支持打印样式 |

## 配置

打开 `index.html`，在脚本开头的 `CONFIG` 里改：

```js
const CONFIG = {
  SITE_TITLE: 'passio博客',         // 站点名称
  AVATAR: 'assets/avatar.jpg',      // 头像（替换 assets/avatar.jpg 即可换头像）
  DOC_ROOT: 'docs',                 // 内容根目录：目录树从这层开始显示，设为 '' 表示当前目录
  DEFAULT_FILE: 'README.md',        // 默认打开的文档（相对于 DOC_ROOT）
  EXCLUDE: [...],                   // 目录树排除规则（正则）
  TOC_LEVELS: [2, 3, 4],            // 右侧大纲提取的标题层级
};
```

`build-manifest.py` 顶部的 `EXCLUDE_FILES` / `EXCLUDE_DIRS` 控制哪些文件不进目录树。

## 目录约定

- 目录树排序：文件夹在前，每个文件夹内 `README.md` 置顶，其余按名称自然排序
- 空文件夹不显示；除 `.md` 外，配置 / 文本 / 图片等文件也会进目录树，点击即可查看
- 建议每个文件夹放一个 `README.md` 作为该分类的首页

## 文件结构

```
你的markdown目录/
├── index.html          # 查看器（唯一必需）
├── assets/vendor/      # 离线依赖（marked + highlight.js）
├── build-manifest.py   # 目录索引生成脚本
├── manifest.json       # 运行 build-manifest.py 后生成
└── 你的文档.md / 子目录/
```

## 常见问题

**双击 index.html 打开是空白 / 提示无法读取目录？**
`file://` 下浏览器禁止 fetch，查看器不支持这种方式；用 nginx 或本地 HTTP 服务托管。

**新增文档没有出现在目录树里？**
目录列表模式（nginx autoindex）下直接刷新页面；manifest 模式下重跑 `python3 build-manifest.py`。

**页面提示"服务端未开启目录列表"？**
nginx 需要在文档目录的 `location` 里加 `autoindex on;` 然后重载配置；不想开就用 manifest 方式。

**搜索很慢？**
搜索索引在第一次搜索时构建，需要读取全部文档。文档量大（几百个以上）时首次搜索会有几秒延迟，之后走缓存。
