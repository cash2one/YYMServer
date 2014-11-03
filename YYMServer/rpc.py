# -*- coding: utf-8 -*-

import time

from sqlalchemy import func
from sqlalchemy.orm import aliased
from werkzeug.security import generate_password_hash, check_password_hash

from flask import jsonify, request, url_for
from flask.ext.restful import reqparse, Resource, fields, marshal_with, marshal, abort
from flask.ext.restful import output_json as restful_output_json
from flask.ext.hmacauth import hmac_auth

from YYMServer import app, db, cache, api, util
from YYMServer.models import *

from flask.ext.restful.representations.json import output_json
output_json.func_globals['settings'] = {'ensure_ascii': False, 'encoding': 'utf8'}


@api.representation('application/json')
def output_json(data, code, headers=None):
    ''' 定制输出内容，固定输出 status 和 message 字段，以方便客户端解析。'''
    message = 'OK'
    if type(data) == dict:
        if data.has_key('message'):
            message = data.pop('message')
        if data.has_key('status'):
            code = data.pop('status')
    data = {'status': code, 'message': message, 'data':data}
    return restful_output_json(data, code, headers)


# 基础接口：
class Version(Resource):
    '''服务器版本查询服务。'''
    def get(self):
        return {'minimal_available_version': 1}

api.add_resource(Version, '/rpc/version')


class Time(Resource):
    '''服务器对时服务。'''
    def get(self):
        return {'timestamp': time.time()}

api.add_resource(Time, '/rpc/time')


class CacheTime(Resource):
    '''服务器缓存时间查询。'''
    def get(self):
        return {'cache_time': app.config['CACHE_DEFAULT_TIMEOUT']}

api.add_resource(CacheTime, '/rpc/cache_time')


# 常用公共辅助：
id_parser = reqparse.RequestParser()
id_parser.add_argument('id', type=int)


class ImageUrl(fields.Raw):
    def format(self, path):
        return url_for('static', filename=path, _external=True)


# 图片信息查询接口：
image_parser = reqparse.RequestParser()
image_parser.add_argument('id', type=int)
image_parser.add_argument('offset', type=int)    # offset 偏移量。
image_parser.add_argument('limit', type=int, default=10)     # limit 限制，与 SQL 语句中的 limit 含义一致。
image_parser.add_argument('site', type=int)      # 指定 POI id，获取所有相关图片
image_parser.add_argument('review', type=int)   # 指定晒单评论 id，获取所有相关图片

image_parser_detail = reqparse.RequestParser()         # 用于创建一个图片上传信息的参数集合
image_parser_detail.add_argument('type', type=int, default=4, required=True)      # 图片分类：1 表示店铺 logo；2 表示店铺门脸图；3 表示用户头像；4 表示评论图片。
image_parser_detail.add_argument('path', type=unicode, required=True)  # 图片保存地址的完整 url （通常应该是云存储地址）
image_parser_detail.add_argument('user', type=int, required=True)      # 图片上传人的账号 id 

image_fields_mini = {
    'id': fields.Integer,
    'url': ImageUrl(attribute='path'),
}

image_fields = {
    'type': fields.Integer,
    'create_time': util.DateTime,    # RFC822-formatted datetime string in UTC
    'user_id': fields.Integer,
}
image_fields.update(image_fields_mini)


# ToDo: 图片上传的接口！
class ImageList(Resource):
    '''提供图片的增、查、删三组服务。'''
    def __repr__(self):
        '''由于 cache.memoize 读取函数参数时，也读取了 self ，因此本类的实例也会被放入 key 的生成过程。
        于是为了函数缓存能够生效，就需要保证 __repr__ 每次提供一个不变的 key。
        '''
        return '%s' % self.__class__.__name__

    @cache.memoize()
    def _get(self, id=None, site=None, review=None):
        query = db.session.query(Image).filter(Image.valid == True)
        if id:
            query = query.filter(Image.id == id)
            return query.all()
        else:
            if review:
                related_review = db.session.query(Review).filter(Review.valid == True).filter(Review.published == True).filter(Review.id == review).first()
                if related_review:
                    return util.get_images(related_review.images or '')
            if site:
                related_reviews = db.session.query(Review).filter(Review.valid == True).filter(Review.published == True).join(Review.site).filter(Site.id == site).order_by(Review.selected.desc()).order_by(Review.publish_time.desc()).all()
                # ToDo: 这里没有充分控制图片的排列顺序！
                image_ids = ' '.join((review.images or '' for review in related_reviews))
                image_ids = ' '.join(set(image_ids.strip().split()))
                related_site = db.session.query(Site).filter(Site.valid == True).filter(Site.id == site).first()
                if related_site:
                    image_ids = (related_site.gate_images or '') + ' ' + image_ids
                return util.get_images(image_ids)
        return []

    @hmac_auth('api')
    @marshal_with(image_fields)
    def get(self):
        args = image_parser.parse_args()
        id = args['id']
        result = self._get(id, args['site'], args['review'])
        offset = args['offset']
        if offset:
            result = result[offset:]
        limit = args['limit']
        if limit:
            result = result[:limit]
        return result

    @hmac_auth('api')
    def delete(self):
        # 不会真正删除信息，只是设置 valid = False ，以便未来查询。
        args = id_parser.parse_args()
        id = args['id']
        image = db.session.query(Image).filter(Image.id == id).filter(Image.valid == True).first()
        if image:
            image.valid = False
            db.session.commit()
            return '', 204
        abort(404, message='Target Image do not exists!')

    @hmac_auth('api')
    def post(self):
        ''' 保存新图片信息的接口。'''
        args = image_parser_detail.parse_args()
        image = Image(valid = True,
                      type = args['type'],      # 这里没有做 type 取值是否在标准集合范围内的判断
                      path = args['path'],
                      create_time = datetime.datetime.now(),
                      user_id = args['user'],
                     )
        db.session.add(image)
        db.session.commit()
        return {'id': image.id}, 201

api.add_resource(ImageList, '/rpc/images')


# 用户登陆接口：
login_parser = reqparse.RequestParser()
login_parser.add_argument('username', type=str, required=True)         # 用户名，只支持 ASCii 字符。
login_parser.add_argument('password', type=str, required=True)    # 密码，只支持 ASCii 字符。
login_parser.add_argument('token', type=str)     # 旧 token，用于迁移登录前发生的匿名行为。
login_parser.add_argument('device', type=str, required=True)      # 设备 id 。

def _generate_token(new_user, device, old_token=None):
    '''辅助函数：根据新登陆的 user 实例创建对应 token。如果提供了旧 token ，相应做旧 token 的历史行为记录迁移。'''
    if old_token:
        old_user = db.session.query(User).join(User.tokens).filter(Token.token == old_token).first()
        if old_user:
            pass        # ToDo: 生成一个后台任务，合并旧 token 的行为数据到当前登陆的新账号！
    # 永远生成新 token，而不复用之前产生的 token。
    token = Token(user_id = new_user.id,
                  device = device,
                  )
    db.session.add(token)
    db.session.commit()
    return token.token


class TokenList(Resource):
    '''用户登陆，并返回账号 token 的接口。'''
    def __repr__(self):
        '''由于 cache.memoize 读取函数参数时，也读取了 self ，因此本类的实例也会被放入 key 的生成过程。
        于是为了函数缓存能够生效，就需要保证 __repr__ 每次提供一个不变的 key。
        '''
        return '%s' % self.__class__.__name__

    @hmac_auth('api')
    def post(self):
        ''' 用户登陆接口。'''
        args = login_parser.parse_args()
        user = db.session.query(User).filter(User.valid == True).filter(User.anonymous == False).filter(User.username == args['username']).first()
        if not user or not check_password_hash(user.password, args['password']):
            abort(403, message='Login Failed!')
        old_token = args['token']
        token = _generate_token(user, args['device'], old_token)
        return {'token': token}, 201

api.add_resource(TokenList, '/rpc/tokens')


# 用户信息查询接口：
user_parser = reqparse.RequestParser()
user_parser.add_argument('id', type=int)
user_parser.add_argument('offset', type=int)    # offset 偏移量。
user_parser.add_argument('limit', type=int, default=10)     # limit 限制，与 SQL 语句中的 limit 含义一致。
user_parser.add_argument('follow', type=int)      # 关注指定 id 所对应用户的账号列表
user_parser.add_argument('fan', type=int)         # 有指定 id 所对应用户作为粉丝的账号列表

user_parser_detail = reqparse.RequestParser()         # 用于创建和更新一个 User 的信息的参数集合
user_parser_detail.add_argument('id', type=int)
user_parser_detail.add_argument('icon', type=int)        # 用户头像对应图片的 id
user_parser_detail.add_argument('name', type=unicode)    # 用户昵称，不能与已有的昵称重复，否则报错。
user_parser_detail.add_argument('mobile', type=str)  # 预留手机号接口，但 App 前端在初期版本不应该允许用户修改！不能与其他用户的手机号重复，否则报错。
user_parser_detail.add_argument('password', type=str)  # 账号密码的明文，至少6个字符。
user_parser_detail.add_argument('gender', type=unicode)    # 用户性别：文字直接表示的“男、女、未知”
user_parser_detail.add_argument('token', type=str)  # 旧 token，用于迁移登录前发生的匿名行为。
user_parser_detail.add_argument('device', type=str)      # 设备 id 。

user_fields_mini = {
    'id': fields.Integer,
    'icon': fields.Nested(image_fields_mini, attribute='icon_image'),   # 用户头像，没有时会变成 id 为 0 的图片
    'name': fields.String,      # 用户昵称
    'level': fields.Integer,    # 用数字表示的用户等级
}
user_fields = {
    'anonymous': fields.Boolean,
    'create_time': util.DateTime,    # 首次创建时间，RFC822-formatted datetime string in UTC
    'update_time': util.DateTime,    # 用户属性修改时间，RFC822-formatted datetime string in UTC
    'username': fields.String,  # 登陆用用户名，App 端会是设备 id（匿名用户）或手机号（已注册用户）
    'mobile': fields.String,    # 用户手机号
    'gender': fields.String,    # 性别：文字直接表示的“男、女、未知”
    'exp': fields.Integer,      # 与用户等级对应的用户经验，需要根据每天的行为日志做更新
    'follow_num': fields.Integer,      # 该用户已关注的账号的数量，是一个缓存值
    'fans_num': fields.Integer,      # 该用户拥有的粉丝数量，是一个缓存值
    'like_num': fields.Integer,      # 该用户喜欢的晒单评论数量，是一个缓存值
    'share_num': fields.Integer,      # 该用户的分享行为数量，是一个缓存值
    'review_num': fields.Integer,      # 该用户发表的晒单评论数量，是一个缓存值
    'favorite_num': fields.Integer,      # 该用户收藏的店铺的数量，是一个缓存值
    'badges': fields.String,    # 用户拥有的徽章名称列表
}
user_fields.update(user_fields_mini)


class UserList(Resource):
    '''对用户账号信息进行查询、注册、修改的服务接口。不提供删除接口。'''
    def __repr__(self):
        '''由于 cache.memoize 读取函数参数时，也读取了 self ，因此本类的实例也会被放入 key 的生成过程。
        于是为了函数缓存能够生效，就需要保证 __repr__ 每次提供一个不变的 key。
        '''
        return '%s' % self.__class__.__name__

    def _format_user(self, user):
        ''' 辅助函数：用于格式化 User 实例，用于接口输出。'''
        user.icon_image = user.icon

    def _check_password(self, password):
        ''' 辅助函数：用于检查用户提交的新密码的合规性。'''
        if len(password) < 6:
            abort(403, message='The password length should be at least 6 characters!')
    
    @cache.memoize()
    def _get(self, id=None, follow=None, fan=None):
        # 当指定用户 id 进行查询时，即使该用户 valid 为 False，也仍然给出详细信息。
        result = []
        if id:
            query = db.session.query(User).filter(User.id == id)
            result = query.all()
        elif follow:
            Main_User = aliased(User)
            query = db.session.query(User).filter(User.valid == True).join(fans, User.id == fans.columns.fan_id).join(Main_User, fans.columns.user_id == Main_User.id).filter(Main_User.id == follow).order_by(fans.columns.action_time.desc())
            result = query.all()
        elif fan:
            Main_User = aliased(User)
            query = db.session.query(User).filter(User.valid == True).join(fans, User.id == fans.columns.user_id).join(Main_User, fans.columns.fan_id == Main_User.id).filter(Main_User.id == fan).order_by(fans.columns.action_time.desc())
            result = query.all()
        [self._format_user(user) for user in result]
        return result

    @hmac_auth('api')
    @marshal_with(user_fields)
    def get(self):
        args = user_parser.parse_args()
        result = self._get(args['id'], args['follow'], args['fan'])
        offset = args['offset']
        if offset:
            result = result[offset:]
        limit = args['limit']
        if limit:
            result = result[:limit]
        return result

    @hmac_auth('api')
    def post(self):
        ''' 用户注册或创建新的匿名用户的接口。'''
        user = None
        args = user_parser_detail.parse_args()
        mobile = args['mobile']
        password = args['password']
        device = args['device']
        if mobile and password:
            has_same_mobile = db.session.query(User).filter(User.mobile == mobile).first()
            if has_same_mobile:
                abort(409, message='This mobile number has been used by another user!')
            self._check_password(password)
            anonymous = False
            username = mobile
        else:   # 匿名用户
            anonymous = True
            mobile = None
            password = None
            # 如果已经存在相同设备 id 的匿名账号，则直接用这个匿名账号登陆并返回 token ！
            user = db.session.query(User).filter(User.valid == True).filter(User.anonymous == True).join(User.tokens).filter(Token.device == device).order_by(Token.id.desc()).first()
            username = unicode(device)
        if user is None:
            user = User(valid = True,
                        anonymous = anonymous,
                        create_time = datetime.datetime.now(),
                        update_time = datetime.datetime.now(),
                        icon_id = args['icon'],
                        name = args['name'],        # name 为空时，Model 会自动生成默认的 name 和 icon 
                        username = username,
                        mobile = mobile,
                        password = password,        # 明文 password 会被 Model 自动加密保存
                        gender = args['gender'],
                       )
            db.session.add(user)
            db.session.commit()
        # 注册后要调用登陆逻辑，返回用户 token 等。
        token = _generate_token(user, device, args['token'], )
        return {'id': user.id, 'token': token}, 201

    @hmac_auth('api')
    def put(self):
        ''' 修改用户详细属性信息的接口。'''
        args = user_parser_detail.parse_args()
        id = args['id']
        user = db.session.query(User).filter(User.id == id).filter(User.valid == True).first()
        if user:
            user.update_time = datetime.datetime.now()
            icon_id = args['icon']
            if icon_id:
                user.icon_id = icon_id
            name = args['name']
            if name:
                has_same_name = db.session.query(User).filter(User.name == name).first()
                if has_same_name and has_same_name.id != id:
                    abort(409, message='The name has been used by another user!')
                user.name = name
            password = args['password']
            if password:
                self._check_password(password)
                user.password = password        # 明文 password 会被 Model 自动加密保存
            gender = args['gender']
            if gender:
                user.gender = gender
            db.session.commit()
            self._format_user(user)
            return marshal(user, user_fields), 201
        abort(404, message='Target User do not exists!')

api.add_resource(UserList, '/rpc/users')


# 首页文章接口：
article_parser = reqparse.RequestParser()
article_parser.add_argument('id', type=int)
article_parser.add_argument('brief', type=int, default=1)     # 大于 0 表示只输出概要信息即可（默认只概要）。
article_parser.add_argument('offset', type=int)    # offset 偏移量。
article_parser.add_argument('limit', type=int, default=10)     # limit 限制，与 SQL 语句中的 limit 含义一致。
article_parser.add_argument('city', type=int)      # 城市 id。

article_fields_brief = {
    'id': fields.Integer,
    'create_time': util.DateTime,    # RFC822-formatted datetime string in UTC
    'title': fields.String,         # 首页文章的标题
    'caption': fields.Nested(image_fields_mini, attribute='caption_image'),     # 首页文章的标题衬图（也即首图）
    'keywords': fields.List(fields.String, attribute='formated_keywords'),      # 概要状态通常只使用第一个关键词
}
article_fields = {
    'update_time': util.DateTime,    # RFC822-formatted datetime string in UTC
    'content': fields.String,         # 首页文章的文本正文，需区分自然段、小标题、图片、店铺链接、分隔符等特殊格式！
    # ToDo: 这里需要和客户端统一一下图文混排的方案！
    'comment_num': fields.Integer,
}
article_fields.update(article_fields_brief)


class ArticleList(Resource):
    '''按城市获取相关首页推荐文章的接口。'''

    def __repr__(self):
        '''由于 cache.memoize 读取函数参数时，也读取了 self ，因此本类的实例也会被放入 key 的生成过程。
        于是为了函数缓存能够生效，就需要保证 __repr__ 每次提供一个不变的 key。
        '''
        return '%s' % self.__class__.__name__

    @cache.memoize()
    def _get(self, brief=None, id=None, city=None):
        # ToDo: Article 表中各计数缓存值的数据没有做动态更新，例如子评论数！
        query = db.session.query(Article).filter(Article.valid == True)
        if id:
            query = query.filter(Article.id == id)
        if city:
            city_object = db.session.query(City).filter(City.valid == True).filter(City.id == city).first()
            country = -1 if not city_object else city_object.country_id
            query_city = query.join(Article.cities).filter(City.id == city)
            query_country = query.join(Article.countries).filter(Country.id == country)
            query = query_city.union(query_country)
        query = query.order_by(Article.order.desc()).order_by(Article.create_time.desc())
        result = []
        for article in query:
            article.caption_image = article.caption
            article.formated_keywords = [] if not article.keywords else article.keywords.strip().split()
            result.append(article)
        return result

    @hmac_auth('api')
    def get(self):
        args = article_parser.parse_args()
        brief = args['brief']
        result = self._get(brief, args['id'], args['city'])
        offset = args['offset']
        if offset:
            result = result[offset:]
        limit = args['limit']
        if limit:
            result = result[:limit]
        if brief:
            return marshal(result, article_fields_brief)
        else:
            return marshal(result, article_fields)

api.add_resource(ArticleList, '/rpc/articles')


# 小贴士接口：
tips_parser = reqparse.RequestParser()
tips_parser.add_argument('id', type=int)
tips_parser.add_argument('brief', type=int, default=1)     # 大于 0 表示只输出概要信息即可（默认只概要）。
tips_parser.add_argument('city', type=int)      # 城市 id。

tips_fields_brief = {
    'id': fields.Integer,
    'default': fields.Boolean,  # 是否是当前城市的默认贴士
    'create_time': util.DateTime,    # RFC822-formatted datetime string in UTC
    'title': fields.String,         # Tips 的标题，用于列表选单，不用于正文显示
}
tips_fields = {
    'update_time': util.DateTime,    # RFC822-formatted datetime string in UTC
    'content': fields.String,         # 小贴士的文本正文，需区分自然段、小标题、分隔符、排序列表等特殊格式！以及支持对其他 Tips 的引用（例如该国家通用的内容）
    # ToDo: 这里需要和客户端统一一下图文混排的方案！
}
tips_fields.update(tips_fields_brief)


class TipsList(Resource):
    '''按城市获取相关小贴士文档的接口。'''

    def __repr__(self):
        '''由于 cache.memoize 读取函数参数时，也读取了 self ，因此本类的实例也会被放入 key 的生成过程。
        于是为了函数缓存能够生效，就需要保证 __repr__ 每次提供一个不变的 key。
        '''
        return '%s' % self.__class__.__name__

    @cache.memoize()
    def _get(self, brief=None, id=None, city=None):
        query = db.session.query(Tips).filter(Tips.valid == True)
        if id:
            query = query.filter(Tips.id == id)
        if city:
            query= query.filter(Tips.city_id == city)
        query = query.order_by(Tips.default.desc())
        result = query.all()
        return result

    @hmac_auth('api')
    def get(self):
        args = tips_parser.parse_args()
        brief = args['brief']
        result = self._get(brief, args['id'], args['city'])
        if brief:
            return marshal(result, tips_fields_brief)
        else:
            return marshal(result, tips_fields)

api.add_resource(TipsList, '/rpc/tips')


# 分类及子分类接口：
category_fields = {
    'id': fields.Integer,
    'name': fields.String,
    'order': fields.Integer,
}

nested_category_fields = {
    'sub_categories': fields.List(fields.Nested(category_fields), attribute='valid_sub_categories'),
}
nested_category_fields.update(category_fields)


class CategoryList(Resource):
    '''获取 POI 分类及子分类列表。'''

    def __repr__(self):
        '''由于 cache.memoize 读取函数参数时，也读取了 self ，因此本类的实例也会被放入 key 的生成过程。
        于是为了函数缓存能够生效，就需要保证 __repr__ 每次提供一个不变的 key。
        '''
        return '%s' % self.__class__.__name__

    @cache.memoize()
    def _get(self, id=None):
        query = db.session.query(Category).filter(Category.valid == True).filter(Category.parent_id == None).order_by(Category.order.desc())
        if id:
            query = query.filter(Category.id == id)
        result = []
        for category in query:
            category.valid_sub_categories = category.children.filter(Category.valid == True).order_by(Category.order.desc()).all()
            result.append(category)
        return result

    @hmac_auth('api')
    @marshal_with(nested_category_fields)
    def get(self):
        args = id_parser.parse_args()
        id = args['id']
        return self._get(id)

api.add_resource(CategoryList, '/rpc/categories')


# 商区接口：
area_fields = {
    'id': fields.Integer,
    'name': fields.String,
    'order': fields.Integer,
    'longitude': fields.Float,
    'latitude': fields.Float,
}


# 城市接口：
city_fields = {
    'id': fields.Integer,
    'name': fields.String,
    'order': fields.Integer,
    'longitude': fields.Float,
    'latitude': fields.Float,
}

nested_city_fields = {
    'areas': fields.List(fields.Nested(area_fields), attribute='valid_areas'),
}
nested_city_fields.update(city_fields)


class CityList(Resource):
    '''获取全部城市及指定城市名字的服务，也可用于查询指定城市下的商圈列表。'''

    def __repr__(self):
        '''由于 cache.memoize 读取函数参数时，也读取了 self ，因此本类的实例也会被放入 key 的生成过程。
        于是为了函数缓存能够生效，就需要保证 __repr__ 每次提供一个不变的 key。
        '''
        return '%s' % self.__class__.__name__

    @cache.memoize()
    def _get(self, id=None):
        query = db.session.query(City).filter(City.valid == True).order_by(City.order.desc())
        if id:
            query = query.filter(City.id == id)
        result = []
        for city in query:
            city.valid_areas = city.areas.filter(Area.valid == True).order_by(Area.order.desc()).all()
            result.append(city)
        return result

    @hmac_auth('api')
    @marshal_with(nested_city_fields)
    def get(self):
        args = id_parser.parse_args()
        id = args['id']
        return self._get(id)

api.add_resource(CityList, '/rpc/cities')


# 国家接口：
country_fields = {
    'id': fields.Integer,
    'name': fields.String,
    'order': fields.Integer,
    'default_city_id': fields.Integer,
    'cities': fields.List(fields.Nested(city_fields), attribute='valid_cities'),
}

class CountryList(Resource):
    '''获取全部国家及指定国家名字的服务，也可用于查询指定国家下属的城市列表。'''

    def __repr__(self):
        '''由于 cache.memoize 读取函数参数时，也读取了 self ，因此本类的实例也会被放入 key 的生成过程。
        于是为了函数缓存能够生效，就需要保证 __repr__ 每次提供一个不变的 key。
        '''
        return '%s' % self.__class__.__name__

    @cache.memoize()
    def _get(self, id=None):
        query = db.session.query(Country).filter(Country.valid == True).order_by(Country.order.desc())
        if id:
            query = query.filter(Country.id == id)
        result = []
        for country in query:
            country.valid_cities = country.cities.filter(City.valid == True).order_by(City.order.desc()).all()
            result.append(country)
        return result

    @hmac_auth('api')
    @marshal_with(country_fields)
    def get(self):
        args = id_parser.parse_args()
        id = args['id']
        return self._get(id)

api.add_resource(CountryList, '/rpc/countries')


# POI 接口：
site_parser = reqparse.RequestParser()
site_parser.add_argument('id', type=int)
site_parser.add_argument('brief', type=int, default=1)     # 大于 0 表示只输出概要信息即可（默认只概要）。
site_parser.add_argument('offset', type=int)    # offset 偏移量。
site_parser.add_argument('limit', type=int, default=10)     # limit 限制，与 SQL 语句中的 limit 含义一致。
site_parser.add_argument('keywords', type=unicode)  # 搜索关键词，空格或英文加号分隔，默认的关系是“且”。搜索时大小写不敏感。
site_parser.add_argument('area', type=int)      # 商圈 id。
site_parser.add_argument('city', type=int)      # 城市 id。
site_parser.add_argument('range', type=int)     # 范围公里数。如果是 -1，则表示“全城”。如果商圈、范围都是空，则表示默认的“智能范围”。
site_parser.add_argument('category', type=int)  # 分类 id。为空则表示“全部分类”。
site_parser.add_argument('order', type=int)     # 0 表示默认的“智能排序”，1 表示“距离最近”（约近约靠前），2 表示“人气最高”（点击量由高到低），3 表示“评价最好”（评分由高到低）。
site_parser.add_argument('longitude', type=float)       # 用户当前位置的经度
site_parser.add_argument('latitude', type=float)        # 用户当前位置的维度

site_fields_mini = {
    'id': fields.Integer,
    'city_name': fields.String,         # POI 所在城市名
    'name': fields.String,
}
site_fields_brief = {
    'logo': fields.Nested(image_fields_mini, attribute='logo_image'),   # 没有时会变成 id 为 0 的图片
    'level': fields.String,
    'stars': fields.Float,
    'review_num': fields.Integer,
    'longitude': fields.Float,
    'latitude': fields.Float,
    'address': fields.String,
    'keywords': fields.List(fields.String, attribute='formated_keywords'),
    'top_images': fields.List(fields.Nested(image_fields_mini), attribute='valid_top_images'),
    'popular': fields.Integer,
}
site_fields_brief.update(site_fields_mini)
site_fields = {
    'name_orig': fields.String,
    'address_orig': fields.String,
    'gate_images': fields.List(fields.Nested(image_fields_mini), attribute='valid_gate_images'),
    'categories': fields.List(fields.String, attribute='valid_categories'),
    'environment': fields.String,       # 空字符串表示没有
    'payment': fields.List(fields.String, attribute='formated_payment_types'),
    'menu': fields.String,      # 空字符串表示没有
    'ticket': fields.String(attribute='formated_ticket'),    # 空字符串表示没有
    'booking': fields.String,   # 空字符串表示没有
    'business_hours': fields.String(attribute='formated_business_hours'),    # 空字符串表示没有
    'phone': fields.String,     # 空字符串表示没有
    'transport': fields.String,         # 空字符串表示没有
    'description': fields.String,       # 空字符串表示没有
    'images_num': fields.Integer,
}
site_fields.update(site_fields_brief)

# ToDo: 欠一个搜索关键字推荐接口！
class SiteList(Resource):
    '''“附近”搜索功能对应的 POI 列表获取。'''
    def __repr__(self):
        '''由于 cache.memoize 读取函数参数时，也读取了 self ，因此本类的实例也会被放入 key 的生成过程。
        于是为了函数缓存能够生效，就需要保证 __repr__ 每次提供一个不变的 key。
        '''
        return '%s' % self.__class__.__name__

    @cache.memoize()
    def _get(self, brief=None, id=None, keywords=None, area=None, city=None, range=None, category=None, order=None, geohash=None):
        # ToDo: Site 表中各计数缓存值的数据没有做动态更新，例如晒单评论数！
        if not area and (range == None or range == 0):
            range = 5   # ToDo: 如果商圈和 range 都没有设置，表示智能范围（注意：range 为 -1 时表示全城搜索）。这里暂时只是把搜索范围置成5公里了。
        query = db.session.query(Site).filter(Site.valid == True)
        if order:
            if order == 1:      # 距离最近：
                pass
            elif order == 2:    # 人气最高：
                query = query.order_by(Site.popular.desc())
            elif order == 3:    # 评价最好：
                query = query.order_by(Site.stars.desc())
            else:       # 这是默认的“智能排序”:
                query = query.order_by(Site.order.desc())
        if id:
            query = query.filter(Site.id == id)
        if area:
            query = query.filter(Site.area_id == area)
        if city:
            query = query.join(Site.area).filter(Area.city_id == city)
            # ToDo: 除了直接使用 city id 判断外，还应该把城市中心点距离一定范围内（即使是属于其他城市的）的 POI 纳入搜索结果！
        if category:
            query = query.join(Site.categories).filter(Category.id == category)
        if keywords:
            # 搜索关键词目前支持在 POI 名称、地址的中文、原文中进行模糊搜索。
            # ToDo: 搜索关键词还应考虑支持 description 和 keywords 两项！
            keywords = keywords.translate({ord('+'):' '})
            keyword_list = keywords.split()
            for keyword in keyword_list:
                query = query.filter(Site.name.ilike(u'%{}%'.format(keyword)) | 
                                     Site.name_orig.ilike(u'%{}%'.format(keyword)) |
                                     Site.address.ilike(u'%{}%'.format(keyword)) |
                                     Site.address_orig.ilike(u'%{}%'.format(keyword)) 
                                    )
        result = []
        for site in query:
            site.stars = site.stars or 0.0      # POI 无星级时输出0，表示暂无评分。
            site.environment = site.environment or u''
            site.formated_payment_types = [] if not site.payment else [payment_types.get(code.lower(), code) for code in site.payment.split()]
            site.menu = site.menu or u''
            site.formated_ticket = u'' if not site.ticket else util.replace_textlib(site.ticket)
            site.booking = site.booking or u''
            site.formated_business_hours = u'' if not site.business_hours else util.replace_textlib(site.business_hours)
            site.phone = site.phone or u''
            site.transport = site.transport or u''
            site.description = site.description or u''
            site.logo_image = site.logo         # 为了缓存能工作
            site.city_name = '' if not site.area else site.area.city.name
            site.formated_keywords = [] if not site.keywords else site.keywords.translate({ord('{'):None, ord('}'):None}).split()
            site.valid_top_images = []
            if site.top_images:
                site.valid_top_images = util.get_images(site.top_images)
            site.valid_top_images = site.valid_top_images[:5]
            if not brief:
                site.valid_gate_images = []
                if site.gate_images:
                    site.valid_gate_images = util.get_images(site.gate_images)
                site.valid_gate_images = site.valid_gate_images[:1]
                site.valid_categories = [category.name for category in site.categories if category.parent_id != None]
            result.append(site)
        return result

    @hmac_auth('api')
    def get(self):
        args = site_parser.parse_args()
        # ToDo: 基于距离范围的搜索暂时没有实现！
        # ToDo: 按距离最近排序暂时没有实现！
        longitude = args['longitude']
        latitude = args['latitude']
        geohash = None
        # 其他基本搜索条件处理：
        brief = args['brief']
        result = self._get(brief, args['id'], args['keywords'], args['area'], args['city'], args['range'], args['category'], args['order'], geohash)
        offset = args['offset']
        if offset:
            result = result[offset:]
        limit = args['limit']
        if limit:
            result = result[:limit]
        if brief:
            return marshal(result, site_fields_brief)
        else:
            return marshal(result, site_fields)

api.add_resource(SiteList, '/rpc/sites')


# 晒单评论接口：
review_parser = reqparse.RequestParser()
review_parser.add_argument('id', type=int)
review_parser.add_argument('brief', type=int, default=1)     # 大于 0 表示只输出概要信息即可（默认只概要）。
review_parser.add_argument('selected', type=int)     # 大于 0 表示只输出置顶信息即可（例如 POI 详情页面中的晒单评论），不够 limit 的要求时，会用非置顶信息补足。
review_parser.add_argument('published', type=int, default=1)     # 大于 0 表示只输出已发表的（默认只已发表的），否则也可输出草稿。
review_parser.add_argument('offset', type=int)    # offset 偏移量。
review_parser.add_argument('limit', type=int, default=10)     # limit 限制，与 SQL 语句中的 limit 含义一致。
review_parser.add_argument('user', type=int)
review_parser.add_argument('site', type=int)    # 相关联的 POI id
review_parser.add_argument('city', type=int)    # 相关联的城市 id

review_parser_detail = reqparse.RequestParser()         # 用于创建和更新一个 Review 的信息的参数集合
review_parser_detail.add_argument('id', type=int)
review_parser_detail.add_argument('published', type=bool, required=True)
review_parser_detail.add_argument('user', type=int, required=True)
review_parser_detail.add_argument('at_list', type=str, required=True)  # 最多允许@ 20 个用户，更多的可能会被丢掉。
review_parser_detail.add_argument('stars', type=float, required=True)
review_parser_detail.add_argument('content', type=unicode, required=True)
review_parser_detail.add_argument('images', type=str, required=True)   # 最多允许绑定 10 张图片，更多的可能会被丢掉。
review_parser_detail.add_argument('keywords', type=unicode, required=True)     # 最多允许键入 15 个关键词，更多的可能会被丢掉。
review_parser_detail.add_argument('total', type=int, required=True)
review_parser_detail.add_argument('currency', type=unicode, required=True)
review_parser_detail.add_argument('site', type=int, required=True)

review_fields_brief = {
    'id': fields.Integer,
    'selected': fields.Boolean,
    'published': fields.Boolean,
    'content': fields.String(attribute='brief_content'),   # brief 模式下，会将文字内容截断到特定长度
    'images': fields.List(fields.Nested(image_fields_mini), attribute='valid_images'),  # brief 模式下，只会提供一张图片
    'like_num': fields.Integer,
    'comment_num': fields.Integer,
    'images_num': fields.Integer,
    'user': fields.Nested(user_fields_mini, attribute='valid_user'),
    'publish_time': util.DateTime,    # RFC822-formatted datetime string in UTC
    'update_time': util.DateTime,    # RFC822-formatted datetime string in UTC
    'total': fields.Integer,
    'currency': fields.String,
    'site': fields.Nested(site_fields_mini, attribute='valid_site'),
}
review_fields = {
    'at_list': fields.List(fields.Nested(user_fields_mini), attribute='valid_at_users'),
    'keywords': fields.List(fields.String, attribute='formated_keywords'),
}
review_fields.update(review_fields_brief)
review_fields['content'] = fields.String        # 非 brief 模式下，提供完整的文字内容

class ReviewList(Resource):
    '''获取某 POI 的晒单评论列表，以及对单独一条晒单评论详情进行查、增、删、改的服务。'''
    def __repr__(self):
        '''由于 cache.memoize 读取函数参数时，也读取了 self ，因此本类的实例也会被放入 key 的生成过程。
        于是为了函数缓存能够生效，就需要保证 __repr__ 每次提供一个不变的 key。
        '''
        return '%s' % self.__class__.__name__

    def _format_review(self, review, brief=None):
        ''' 辅助函数：用于格式化 Review 实例，用于接口输出。'''
        review.valid_user = review.user
        review.valid_user.icon_image = review.user.icon
        review.valid_site = review.site
        if review.site:
            review.valid_site.city_name = '' if not review.site.area else review.site.area.city.name
        review.images_num = 0 if not review.images else len(review.images.split())
        review.currency = review.currency or u'人民币'
        review.formated_keywords = [] if not review.keywords else review.keywords.split()
        review.valid_at_users = []
        if review.at_list:
            review.valid_at_users = util.get_users(review.at_list)
        review.valid_images = []
        if review.images:
            review.valid_images = util.get_images(review.images)
        if brief:
            review.brief_content = review.content[:80]
            review.valid_images = review.valid_images[:1]

    @cache.memoize()
    def _get(self, brief=None, selected = None, published = None, id=None, site=None, city=None, user=None):
        # ToDo: Review 表中各计数缓存值的数据没有做动态更新，例如“赞”数！
        query = db.session.query(Review).filter(Review.valid == True)
        query = query.order_by(Review.publish_time.desc())
        if id:
            query = query.filter(Review.id == id)
        if user:
            query = query.filter(Review.user_id == user)
        if site:
            query = query.filter(Review.site_id == site)
        if city:
            # ToDo: 搜索 POI 的时候，会把某城市中心点一定范围内的 POI （尽管是别的城市的）也放进来，那么搜 Review 时候是否也应该支持这个？
            query = query.join(Review.site).join(Site.area).filter(Area.city_id == city)
        result = []
        if selected == None:
            # ToDo: 后台需要有个定时任务，将被关注多的 Review 设置成 selected 。
            pass
        else:   # 要求只返回 selected 或者只返回一定没被 selected 的内容时：
            query = query.filter(Review.selected == selected)   # selected 取值为合法 boolean 这一点，由 get(self) 函数调用 _get 前负责保证！
        if published:
            query = query.filter(Review.published == True)
        for review in query:
            self._format_review(review, brief)
            result.append(review)
        return result

    @hmac_auth('api')
    def get(self):
        args = review_parser.parse_args()
        brief = args['brief']
        selected = args['selected']
        limit = args['limit']
        if selected:
            # 如果 selected 数量不够，就得用没被 selected 的内容来补。
            result = self._get(brief, True, args['published'], args['id'], args['site'], args['city'], args['user'])
            if limit and len(result) < limit:
                result += self._get(brief, False, args['published'], args['id'], args['site'], args['city'], args['user'])
        else:
            result = self._get(brief, None, args['published'], args['id'], args['site'], args['city'], args['user'])
        offset = args['offset']
        if offset:
            result = result[offset:]
        if limit:
            result = result[:limit]
        if brief:
            return marshal(result, review_fields_brief)
        else:
            return marshal(result, review_fields)

    @hmac_auth('api')
    def delete(self):
        # 不会真正删除信息，只是设置 valid = False ，以便未来查询。
        args = id_parser.parse_args()
        id = args['id']
        review = db.session.query(Review).filter(Review.id == id).filter(Review.valid == True).first()
        if review:
            review.valid = False
            db.session.commit()
            return '', 204
        abort(404, message='Target Review do not exists!')

    @hmac_auth('api')
    def post(self):
        ''' 创建新晒单评论的接口。'''
        args = review_parser_detail.parse_args()
        at_list = util.truncate_list(args['at_list'], 200, 20)
        images = util.truncate_list(args['images'], 200, 10)
        keywords = util.truncate_list(args['keywords'], 200, 15)
        keywords = keywords if not keywords or len(keywords) < 200 else keywords[:200]
        review = Review(valid = True,
                        published = args['published'],
                        update_time = datetime.datetime.now(),
                        user_id = args['user'],
                        at_list = at_list,
                        stars = args['stars'],
                        content = args['content'],
                        images = images,
                        keywords = keywords,
                        total = args['total'],
                        currency = args['currency'],    # 这里没有做币种文字是否在有效范围内的判断
                        site_id = args['site'],
                       )
        if args['published']:
            review.publish_time = datetime.datetime.now()
        db.session.add(review)
        db.session.commit()
        return {'id': review.id}, 201

    @hmac_auth('api')
    def put(self):
        ''' 修改晒单评论内容的接口。'''
        args = review_parser_detail.parse_args()
        id = args['id']
        review = db.session.query(Review).filter(Review.id == id).filter(Review.valid == True).first()
        if review:
            at_list = util.truncate_list(args['at_list'], 200, 20)
            images = util.truncate_list(args['images'], 200, 10)
            keywords = util.truncate_list(args['keywords'], 200, 15)
            keywords = keywords if not keywords or len(keywords) < 200 else keywords[:200]
            review.published = args['published']
            review.update_time = datetime.datetime.now()
            review.user_id = args['user']
            review.at_list = at_list
            review.stars = args['stars']
            review.content = args['content']
            review.images = images
            review.keywords = keywords
            review.total = args['total']
            review.currency = args['currency']    # 这里没有做币种文字是否在有效范围内的判断
            review.site_id = args['site']
            if args['published'] and not review.publish_time:   # 只有首次发布才记录 publish_time 
                review.publish_time = datetime.datetime.now()
            db.session.commit()
            self._format_review(review, brief=0)
            return marshal(review, review_fields), 201
        abort(404, message='Target Review do not exists!')


api.add_resource(ReviewList, '/rpc/reviews')


# 二级子评论接口：
comment_parser = reqparse.RequestParser()
comment_parser.add_argument('id', type=int)
comment_parser.add_argument('offset', type=int)    # offset 偏移量。
comment_parser.add_argument('limit', type=int, default=10)     # limit 限制，与 SQL 语句中的 limit 含义一致。
comment_parser.add_argument('article', type=int)      # 指定推荐文章的 id，获取所有相关子评论
comment_parser.add_argument('review', type=int)         # 指定晒单评论 id，获取所有相关子评论

comment_parser_detail = reqparse.RequestParser()         # 用于创建和更新一个 Comment 的信息的参数集合
comment_parser_detail.add_argument('id', type=int)
comment_parser_detail.add_argument('review', type=int, required=True)
comment_parser_detail.add_argument('article', type=int, required=True)
comment_parser_detail.add_argument('user', type=int, required=True)
comment_parser_detail.add_argument('at_list', type=str, required=True)  # 最多允许@ 20 个用户，更多的可能会被丢掉。
comment_parser_detail.add_argument('content', type=unicode, required=True)

comment_fields = {
    'id': fields.Integer,
    'publish_time': util.DateTime,    # RFC822-formatted datetime string in UTC
    'update_time': util.DateTime,    # RFC822-formatted datetime string in UTC
    'review_id': fields.Integer,        # 绑定的晒单评论 id
    'article_id': fields.Integer,        # 绑定的首页文章 id
    'user': fields.Nested(user_fields_mini, attribute='valid_user'),
    'at_list': fields.List(fields.Nested(user_fields_mini), attribute='valid_at_users'),        # 子评论通常只允许 @ 一个人，但为了界面一致，仍然用列表输出。
    'content': fields.String,   
}


class CommentList(Resource):
    '''获取某晒单评论的子评论列表，或者进行增、删、改的服务。'''
    def __repr__(self):
        '''由于 cache.memoize 读取函数参数时，也读取了 self ，因此本类的实例也会被放入 key 的生成过程。
        于是为了函数缓存能够生效，就需要保证 __repr__ 每次提供一个不变的 key。
        '''
        return '%s' % self.__class__.__name__

    def _format_comment(self, comment):
        ''' 辅助函数：用于格式化 Comment 实例，用于接口输出。'''
        comment.valid_user = comment.user
        comment.valid_at_users = util.get_users(comment.at_list or '')
    
    @cache.memoize()
    def _get(self, id=None, article=None, review=None):
        query = db.session.query(Comment).filter(Comment.valid == True)
        query = query.order_by(Comment.publish_time.desc())
        if id:
            query = query.filter(Comment.id == id)
        if article:
            query = query.filter(Comment.article_id == article)
        if review:
            query = query.filter(Comment.review_id == review)
        result = []
        for comment in query:
            self._format_comment(comment)
            result.append(comment)
        return result

    @hmac_auth('api')
    @marshal_with(comment_fields)
    def get(self):
        args = comment_parser.parse_args()
        result = self._get(args['id'], args['article'], args['review'])
        offset = args['offset']
        if offset:
            result = result[offset:]
        limit = args['limit']
        if limit:
            result = result[:limit]
        return result

    @hmac_auth('api')
    def delete(self):
        # 不会真正删除信息，只是设置 valid = False ，以便未来查询。
        args = id_parser.parse_args()
        id = args['id']
        review = db.session.query(Comment).filter(Comment.id == id).filter(Comment.valid == True).first()
        if review:
            review.valid = False
            db.session.commit()
            return '', 204
        abort(404, message='Target Comment do not exists!')

    @hmac_auth('api')
    def post(self):
        ''' 创建新的子评论的接口。'''
        args = comment_parser_detail.parse_args()
        at_list = util.truncate_list(args['at_list'], 200, 20)
        comment = Comment(valid = True,
                          publish_time = datetime.datetime.now(),
                          update_time = datetime.datetime.now(),
                          review_id = args['review'],
                          article_id = args['article'],
                          user_id = args['user'],
                          at_list = at_list,
                          content = args['content'],
                         )
        db.session.add(comment)
        db.session.commit()
        return {'id': comment.id}, 201

    @hmac_auth('api')
    def put(self):
        ''' 修改晒单评论内容的接口。'''
        args = comment_parser_detail.parse_args()
        id = args['id']
        comment = db.session.query(Comment).filter(Comment.id == id).filter(Comment.valid == True).first()
        if comment:
            at_list = util.truncate_list(args['at_list'], 200, 20)
            comment.update_time = datetime.datetime.now()
            comment.review_id = args['review']
            comment.article_id = args['article']
            comment.user_id = args['user']
            comment.at_list = at_list
            comment.content = args['content']
            db.session.commit()
            self._format_comment(comment)
            return marshal(comment, comment_fields), 201
        abort(404, message='Target Comment do not exists!')

api.add_resource(CommentList, '/rpc/comments')


# ToDo: 应该做一个发全局通知的接口，避免很多不登陆的用户创建大量的用户消息记录（由于每个消息需要保存每个用户的已读、未读记录）。

# 用户消息对话线索接口
message_parser = reqparse.RequestParser()
message_parser.add_argument('stop', type=int, default=0)   # 截止 message id，也即返回数据只考虑 id 大于这一指定值的 message 消息。（注意：分批读取时每次请求的截止 message id 不能轻易变化，否则会使缓存失效！而应该使用 offset 来控制！）
message_parser.add_argument('offset', type=int)    # offset 偏移量。
message_parser.add_argument('limit', type=int, default=10)     # limit 限制，与 SQL 语句中的 limit 含义一致。
message_parser.add_argument('user', type=int, required=True)      # 仅获取这一指定用户的消息
message_parser.add_argument('thread', type=str)         # 仅获取这一指定对话线索的消息

message_fields_thread = {
    'id': fields.Integer,       # 当前对话线索中，最新一条的消息 id
    'thread': fields.String(attribute='group_key'),        # 对话线索标识，也即后台数据库中的 group_key （私信消息分组快捷键，将本消息相关 user_id 按从小到大排序，用“_”连接作为 Key）
    'create_time': util.DateTime,    # RFC822-formatted datetime string in UTC
    'sender': fields.Nested(user_fields_mini, attribute='valid_sender'),        # 发送人的账号信息
    'content': fields.String,   # 消息文本正文，如果是系统发送的消息，则可能存在应用内资源的跳转链接。（截取前 100 个字符差不多够了吧？）
    'unread': fields.Integer,   # 该线索的未读消息数
}


class MessageThreadList(Resource):
    '''获取对话线索的列表，每个线索提供信息概要和未读数。'''
    def __repr__(self):
        '''由于 cache.memoize 读取函数参数时，也读取了 self ，因此本类的实例也会被放入 key 的生成过程。
        于是为了函数缓存能够生效，就需要保证 __repr__ 每次提供一个不变的 key。
        '''
        return '%s' % self.__class__.__name__

    @cache.memoize(50)  # 缓存时间只能设置得非常短，要不新消息会延迟收到。
    def _get(self, stop=0, user=None, thread=None):     # thread 接口的 stop 更新逻辑，需要是没有未读消息时，记录当时的最大 message id 作为下一次 stop 的值。
        # 计算未读数：
        unread_dic = {}
        query = db.session.query(UserReadMessage.id, Message.group_key, func.count(Message.id)).join(UserReadMessage.message).filter(Message.valid == True).filter(Message.id > stop).filter(UserReadMessage.has_read == False)
        if user:
            query = query.filter(UserReadMessage.user_id == user)
        if thread:
            query = query.filter(Message.group_key == thread)
        query = query.group_by(Message.group_key)
        for row in query:
            user_read_message_id, group_key, unread_count = row
            unread_dic[group_key] = unread_count
        # 准备消息线索：
        query = db.session.query(Message).filter(Message.valid == True).filter(Message.id > stop)
        if user:
            query = query.join(Message.users).filter(User.id == user)
        if thread:
            query = query.filter(Message.group_key == thread)
        query = query.order_by(Message.create_time.desc())      # 每个对话组显示最新一条的详情
        query = db.session.query().add_entity(Message, alias=query.subquery()).group_by('group_key')         # 让 order_by 比 group_by 更早生效！
        result = []
        for thread in query:
            thread.valid_sender = thread.sender_user
            thread.unread = unread_dic.get(thread.group_key, 0)         # 输出未读数
            result.append(thread)
        result.reverse()        # 输出时旧的 Thread 先输出，以便分批读取。
        return result

    @hmac_auth('api')
    @marshal_with(message_fields_thread)
    def get(self):
        args = message_parser.parse_args()
        result = self._get(args['stop'], args['user'], args['thread'])
        offset = args['offset']
        if offset:
            result = result[offset:]
        limit = args['limit']
        if limit:
            result = result[:limit]
        return result

api.add_resource(MessageThreadList, '/rpc/messages/threads')


# 用户消息接口
message_parser_detail = reqparse.RequestParser()         # 用于创建新 message 信息的参数集合
message_parser_detail.add_argument('sender', type=int, required=True)     # 消息发送人
message_parser_detail.add_argument('receiver', type=int, required=True)     # 消息接收人
message_parser_detail.add_argument('content', type=unicode, required=True)      # 消息文本正文，如果是系统发送的消息，则可能存在应用内资源的跳转链接。

message_fields = {
    'id': fields.Integer,
    'create_time': util.DateTime,    # RFC822-formatted datetime string in UTC
    'sender_id': fields.Integer,        # 发送人的 user id （message 详情通常用于提取一个对话线索中的详细消息，因此 user 的详细属性就不展开了。）
    'content': fields.String,   # 消息文本正文，如果是系统发送的消息，则可能存在应用内资源的跳转链接。
    'thread': fields.String(attribute='group_key'),        # 对话线索标识。其实是冗余的，因为在参数里通常已经指定 thread 了，但再次显示用于确认。
}


class MessageList(Resource):
    '''获取用户消息详情的列表。并能够通过这一接口创建新用户消息。'''
    # 暂时不提供删除、修改操作。
    def __repr__(self):
        '''由于 cache.memoize 读取函数参数时，也读取了 self ，因此本类的实例也会被放入 key 的生成过程。
        于是为了函数缓存能够生效，就需要保证 __repr__ 每次提供一个不变的 key。
        '''
        return '%s' % self.__class__.__name__

    @cache.memoize(50)  # 缓存时间只能设置得非常短，要不新消息会延迟收到。
    def _get(self, stop=0, user=None, thread=None):     # Message 接口的 stop 值，只需考虑当前对话线索历史读取过的最大 id 值即可。
        query = db.session.query(Message).filter(Message.valid == True).filter(Message.id > stop)
        if user:
            query = query.join(Message.users).filter(User.id == user)
        if thread:
            query = query.filter(Message.group_key == thread)
        query = query.order_by(Message.id)      # 从旧的消息开始显示，以便分组读取以及设置 stop
        result = []
        for message in query:
            message.sender_id = message.sender_user_id
            result.append(message)
        return result

    @hmac_auth('api')
    @marshal_with(message_fields)
    def get(self):
        args = message_parser.parse_args()
        user = args['user']
        result = self._get(args['stop'], user, args['thread'])
        offset = args['offset']
        if offset:
            result = result[offset:]
        limit = args['limit']
        if limit:
            result = result[:limit]
        # 已发送完的消息将被设置为已读状态：
        message_ids = [message.id for message in result]
        unread_messages = db.session.query(UserReadMessage).filter(UserReadMessage.has_read == False).filter(UserReadMessage.user_id == user).filter(UserReadMessage.message_id.in_(message_ids)).all()
        for unread_message in unread_messages:
            unread_message.has_read = True
        db.session.commit()
        return result

    @hmac_auth('api')
    def post(self):
        ''' 创建新的用户消息的接口。'''
        args = message_parser_detail.parse_args()
        # ToDo: 这里没有对 sender 和 receiver 是否是合法的用户账号进行检查，也即需要客户端保证。
        sender = args['sender']
        receiver = args['receiver']
        user_ids = [sender, receiver]
        user_ids.sort()
        group_key = '_'.join(map(str, user_ids))
        message = Message(valid = True,
                          create_time = datetime.datetime.now(),
                          sender_user_id = sender,
                          content = args['content'],
                          group_key = group_key,
                         )
        db.session.add(message)
        db.session.commit()
        message_id = message.id
        read_record = UserReadMessage(user_id = sender,
                                      message_id = message_id,
                                      has_read = True,
                                      )
        db.session.add(read_record)
        read_record = UserReadMessage(user_id = receiver,
                                      message_id = message_id,
                                      has_read = False,
                                      )
        db.session.add(read_record)
        db.session.commit()
        return {'id': message_id}, 201

api.add_resource(MessageList, '/rpc/messages')


# 用户消息未读数接口
message_parser_unread = reqparse.RequestParser()         
message_parser_unread.add_argument('user', type=int, required=True)     # 仅获取此指定用户的消息
message_parser_unread.add_argument('thread', type=str)   # 对话线索标识，也即后台数据库中的 group_key （私信消息分组快捷键，将本消息相关 user_id 按从小到大排序，用“_”连接作为 Key）

message_fields_unread = {
    'thread': fields.String,        # 对话线索标识，为空时标识是该用户的全部维度消息数
    'unread': fields.Integer,   # 指定用户的未读消息数。当前版本只返回 0 或 1，而不会给准确的具体数字以降低计算量
}


class MessageUnreadList(Resource):
    '''获取指定用户的未读消息信息，当前版本暂时只提供了未读数。'''
    # 为节省性能，应该只返回是否有未读数就行了。
    def __repr__(self):
        '''由于 cache.memoize 读取函数参数时，也读取了 self ，因此本类的实例也会被放入 key 的生成过程。
        于是为了函数缓存能够生效，就需要保证 __repr__ 每次提供一个不变的 key。
        '''
        return '%s' % self.__class__.__name__

    # 这个接口我认为不需要缓存！ @cache.memoize(50)  # 缓存时间只能设置得非常短，要不新消息会延迟收到。
    def _get(self, user=None, thread=None):
        query = db.session.query(UserReadMessage).join(UserReadMessage.message).filter(Message.valid == True).filter(UserReadMessage.has_read == False)
        if user:
            query = query.filter(UserReadMessage.user_id == user)
        if thread:
            query = query.filter(Message.group_key == thread)
        has_unread = query.first()
        unread = 1 if has_unread else 0
        return {'thread': thread,
                'unread': unread,
                }

    @hmac_auth('api')
    @marshal_with(message_fields_unread)
    def get(self):
        args = message_parser_unread.parse_args()
        return self._get(args['user'], args['thread'])

api.add_resource(MessageUnreadList, '/rpc/messages/unread')


# ==== json 网络服务样例 ====
cac_parser = reqparse.RequestParser()
cac_parser.add_argument('a', type=int, help=u'被相加的第一个数字')
cac_parser.add_argument('b', type=int, help=u'被相加的第二个数字')


class Calculator(Resource):
    @hmac_auth('demo')
    def get(self):
        args = cac_parser.parse_args()
        return {'restful_result': args['a'] + args['b']}

api.add_resource(Calculator, '/rpc/accumulator')


