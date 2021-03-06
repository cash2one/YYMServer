# -*- coding: utf-8 -*-

DEBUG = False
# 显示共享数据的 html5 页面链接基础路径：
BASEURL_SHARE = 'http://h5.youyoumm.com/share'
# 设置时区：
""" 服务器时区应设置为 'Asia/Shanghai' ，否则可能数据出错！"""
# 强制 json 输出采用 utf-8 ：
JSON_AS_ASCII = False
# Create dummy secrey key so we can use flash
SECRET_KEY = 'YouYouMM_and_KeshaQ_SEC_KEY_408'
# 七牛 key：
QINIU_ACCESS_KEY = 'SHOULD_REPLACE_TO_REAL_QINIU_ACCESS_KEY'
QINIU_SECRET_KEY = 'SHOULD_REPLACE_TO_REAL_QINIU_SECRET_KEY'
QINIU_BUCKET = 'youyoumm'
QINIU_CALLBACK = 'http://rpc.youyoumm.com/rpc/images/call'
# 环信 key：
EASEMOB_ORG = 'easemob-playground'
EASEMOB_APP = 'test1'
EASEMOB_CLIENT_ID = 'YXA6wDs-MARqEeSO0VcBzaqg5A'
EASEMOB_CLIENT_SECRET = 'YXA6JOMWlLap_YbI_ucz77j-4-mI0JA'
# 天气 key：
WEATHER_KEY_WU = 'SHOULD_REPLACE_TO_REAL_WUNDERGROUND_WEATHER_KEY'
# 设置静态文件（主要是图片）存储路径
# 注意！！需要将 YYMServer/files 目录下的 images, js, style 三个目录做符号链接到静态文件存储目录，
#         以便微信分享出去的网页可以调用。
STATIC_FOLDER = 'files'
# 数据库连接设置：
SQLALCHEMY_DATABASE_URI = 'mysql://root:root@127.0.0.1:8889/keshaq'
# Cache 服务设置：详细参数参考 http://pythonhosted.org/Flask-Cache/
CACHE_TYPE = 'simple'
CACHE_DEFAULT_TIMEOUT = 15 * 60
#CACHE_TYPE = 'redis'
#CACHE_REDIS_HOST = '127.0.0.1'
#CACHE_REDIS_PORT = 6379
#CACHE_REDIS_PASSWORD = ''


