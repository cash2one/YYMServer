# -*- coding: utf-8 -*-

import time

from flask import jsonify, request
from flask.ext.restful import reqparse, Resource, fields, marshal_with, marshal
from flask.ext.hmacauth import hmac_auth

from YYMServer import app, db, cache, api
from YYMServer.models import *

from flask.ext.restful.representations.json import output_json
output_json.func_globals['settings'] = {'ensure_ascii': False, 'encoding': 'utf8'}


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


# 常用公共辅助：
id_parser = reqparse.RequestParser()
id_parser.add_argument('id', type=int)


# 分类及子分类接口：
category_fields = {
    'id':fields.Integer,
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
    'id':fields.Integer,
    'name': fields.String,
    'order': fields.Integer,
    'longitude': fields.Float,
    'latitude': fields.Float,
}


# 城市接口：
city_fields = {
    'id':fields.Integer,
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
    'id':fields.Integer,
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
site_parser.add_argument('brief', type=int)     # 大于 0 表示只输出概要信息即可。
site_parser.add_argument('offset', type=int)    # offset 偏移量。
site_parser.add_argument('limit', type=int)     # limit 限制，与 SQL 语句中的 limit 含义一致。
site_parser.add_argument('id', type=int)
site_parser.add_argument('keywords', type=str)  # 搜索关键词，空格分隔，默认的关系是“且”。
site_parser.add_argument('area', type=int)      # 商圈 id。
site_parser.add_argument('city', type=int)      # 城市 id。
site_parser.add_argument('range', type=int)     # 范围公里数。如果是 -1，则表示“全城”。如果城市、商圈、范围都是空，则表示默认的“智能范围”。
site_parser.add_argument('category', type=int)  # 分类 id。为空则表示“全部分类”。
site_parser.add_argument('order', type=int)     # 0 表示默认的“智能排序”，1 表示“距离最近”（约近约靠前），2 表示“人气最高”（点击量由高到低），3 表示“评价最好”（评分由高到低）。


site_fields_brief = {
    'name': fields.String,
    'address': fields.String,
    'business_hours': fields.String,
    'description': fields.String,
}
site_fields = {}
site_fields.update(site_fields_brief)

class SiteList(Resource):
    '''“附近”搜索功能对应的 POI 列表获取。'''
#    @hmac_auth('api')
#    @marshal_with(site_fields)
    def get(self):
        results = db.session.query(Site).first()
        return marshal(results, site_fields)

api.add_resource(SiteList, '/rpc/sites')


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


