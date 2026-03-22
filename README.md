# Qwen2Api

本质上是利用Qwen Code的免费额度，本身也可以使用Openai格式调用，但access_token有效期太短了也相对难获取，所以我做了这个，支持使用账号密码调用，现在网页聊天的逆向也回来了，两个都可以用。

### Todo:

* [X] 支持 `/v1/models`
* [X] 研究下网页端还行不行（？）

## 快速搭建

1. 克隆此项目

```bash
$ git clone https://github.com/jyz2012/qwen2api.git
```

2. 安装所需的库

```bash
# Use uv
$ uv sync
# Use pip
$ pip install -r requirements.txt
```

3. 运行服务

```bash
$ python app.py
# 单独运行QwenCode服务
$ python code.py
# 单独运行QwenChat逆向服务
$ python chat.py
```

### API调用

Cherry Studio里面都能用，baseurl是 ` /v1/chat/comletions`.模型有  `vision-model `和 `coder-model` 还有网页聊天里的模型.

### APIkey获取

登录使用的邮箱密码即为APIKey，格式为：`email:password`

~~PS: 之前发现不能用了因为不用就一直懒得管，现在又有需求了发现改两下（就是重写了）还能使，感谢阿里。~~
