# Qwen2Api

本质上是利用Qwen Code的免费额度，本身也可以使用Openai格式调用，但access_token有效期太短了也相对难获取，所以我做了这个，支持使用网页聊天的token调用。

### Todo:

* [ ] 支持 `/v1/models`
* [ ] 研究下网页端还行不行（？）

## 快速搭建

1. 克隆此项目

```bash
git clone https://github.com/jyz2012/qwen2api.git
```

2. 安装所需的库（使用uv）

```bash
uv sync
```

3. 运行服务

```bash
uv run app.py
```

我不管了！Cherry Studio里面都能用！模型有 `vision-model`和 `coder-model` .

### APIkey获取

登录的使用的邮箱密码即为APIKey 格式为：`email:password`

~~PS: 之前发现不用了就一直懒得管，现在又有需求了发现改两下（就是重写了）还能使，感谢阿里。~~
