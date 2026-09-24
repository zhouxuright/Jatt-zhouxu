"""Seed Neo4j knowledge graph with Chinese legal knowledge.

Populates the Neo4j graph database with:
- ~31 major Chinese laws (法律)
- Key legal articles (法条) from seed data
- Legal concepts (概念) covering major areas of Chinese law
- Court levels (法院) representing the judicial hierarchy
- Relationships: CONTAINS, REFERENCES, CONCEPT, CATEGORY

Usage:
    python backend/scripts/seed_neo4j.py

Requirements:
    pip install neo4j
"""

import json
import sys
import os
from pathlib import Path
from datetime import datetime

# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------
try:
    from neo4j import GraphDatabase
except ImportError:
    print("[ERROR] neo4j Python driver is not installed.")
    print("        Install it with:  pip install neo4j")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
NEO4J_URI = "bolt://neo4j:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "legalpassword"

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LAWS_JSON_PATH = PROJECT_ROOT / "backend" / "data" / "datasets" / "laws.json"

# ---------------------------------------------------------------------------
# Seed data: ~31 major Chinese laws
# ---------------------------------------------------------------------------
MAJOR_LAWS = [
    {
        "name": "中华人民共和国宪法",
        "type": "宪法",
        "effective_date": "2018-03-11",
        "status": "有效",
        "category": "宪法及宪法相关法",
        "description": "国家的根本法，具有最高的法律效力",
    },
    {
        "name": "中华人民共和国民法典",
        "type": "法律",
        "effective_date": "2021-01-01",
        "status": "有效",
        "category": "民法商法",
        "description": "调整平等主体的自然人、法人和非法人组织之间的人身关系和财产关系",
    },
    {
        "name": "中华人民共和国刑法",
        "type": "法律",
        "effective_date": "2020-12-26",
        "status": "有效",
        "category": "刑法",
        "description": "规定犯罪、刑事责任和刑罚的法律",
    },
    {
        "name": "中华人民共和国刑事诉讼法",
        "type": "法律",
        "effective_date": "2018-10-26",
        "status": "有效",
        "category": "诉讼与非诉讼程序法",
        "description": "规定刑事诉讼程序的法律",
    },
    {
        "name": "中华人民共和国民事诉讼法",
        "type": "法律",
        "effective_date": "2021-12-24",
        "status": "有效",
        "category": "诉讼与非诉讼程序法",
        "description": "规定民事诉讼程序的法律",
    },
    {
        "name": "中华人民共和国行政诉讼法",
        "type": "法律",
        "effective_date": "2017-06-27",
        "status": "有效",
        "category": "诉讼与非诉讼程序法",
        "description": "规定行政诉讼程序的法律",
    },
    {
        "name": "中华人民共和国公司法",
        "type": "法律",
        "effective_date": "2023-12-29",
        "status": "有效",
        "category": "民法商法",
        "description": "规范公司的设立、组织、运营和解散的法律",
    },
    {
        "name": "中华人民共和国劳动合同法",
        "type": "法律",
        "effective_date": "2012-12-28",
        "status": "有效",
        "category": "社会法",
        "description": "完善劳动合同制度，保护劳动者合法权益",
    },
    {
        "name": "中华人民共和国行政许可法",
        "type": "法律",
        "effective_date": "2004-07-01",
        "status": "有效",
        "category": "行政法",
        "description": "规范行政许可的设定和实施",
    },
    {
        "name": "中华人民共和国行政处罚法",
        "type": "法律",
        "effective_date": "2021-07-15",
        "status": "有效",
        "category": "行政法",
        "description": "规范行政处罚的设定和实施",
    },
    {
        "name": "中华人民共和国合同法",
        "type": "法律",
        "effective_date": "1999-10-01",
        "status": "已废止",
        "category": "民法商法",
        "description": "已纳入民法典合同编",
    },
    {
        "name": "中华人民共和国物权法",
        "type": "法律",
        "effective_date": "2007-10-01",
        "status": "已废止",
        "category": "民法商法",
        "description": "已纳入民法典物权编",
    },
    {
        "name": "中华人民共和国侵权责任法",
        "type": "法律",
        "effective_date": "2010-07-01",
        "status": "已废止",
        "category": "民法商法",
        "description": "已纳入民法典侵权责任编",
    },
    {
        "name": "中华人民共和国婚姻法",
        "type": "法律",
        "effective_date": "2001-04-28",
        "status": "已废止",
        "category": "民法商法",
        "description": "已纳入民法典婚姻家庭编",
    },
    {
        "name": "中华人民共和国继承法",
        "type": "法律",
        "effective_date": "1985-10-01",
        "status": "已废止",
        "category": "民法商法",
        "description": "已纳入民法典继承编",
    },
    {
        "name": "中华人民共和国立法法",
        "type": "法律",
        "effective_date": "2023-03-13",
        "status": "有效",
        "category": "宪法及宪法相关法",
        "description": "规范立法活动的法律",
    },
    {
        "name": "中华人民共和国国家安全法",
        "type": "法律",
        "effective_date": "2015-07-01",
        "status": "有效",
        "category": "行政法",
        "description": "维护国家安全的法律",
    },
    {
        "name": "中华人民共和国反不正当竞争法",
        "type": "法律",
        "effective_date": "2019-04-23",
        "status": "有效",
        "category": "经济法",
        "description": "制止不正当竞争行为，保护经营者和消费者合法权益",
    },
    {
        "name": "中华人民共和国反垄断法",
        "type": "法律",
        "effective_date": "2022-08-01",
        "status": "有效",
        "category": "经济法",
        "description": "预防和制止垄断行为，保护市场公平竞争",
    },
    {
        "name": "中华人民共和国知识产权法",
        "type": "法律",
        "effective_date": "2020-11-11",
        "status": "有效",
        "category": "民法商法",
        "description": "保护知识产权的法律体系（含专利法、商标法、著作权法）",
    },
    {
        "name": "中华人民共和国专利法",
        "type": "法律",
        "effective_date": "2021-06-01",
        "status": "有效",
        "category": "民法商法",
        "description": "保护发明创造专利权",
    },
    {
        "name": "中华人民共和国商标法",
        "type": "法律",
        "effective_date": "2019-11-01",
        "status": "有效",
        "category": "民法商法",
        "description": "加强商标管理，保护商标专用权",
    },
    {
        "name": "中华人民共和国著作权法",
        "type": "法律",
        "effective_date": "2021-06-01",
        "status": "有效",
        "category": "民法商法",
        "description": "保护文学、艺术和科学作品作者的著作权",
    },
    {
        "name": "中华人民共和国环境保护法",
        "type": "法律",
        "effective_date": "2015-01-01",
        "status": "有效",
        "category": "经济法",
        "description": "保护和改善环境，防治污染和其他公害",
    },
    {
        "name": "中华人民共和国土地管理法",
        "type": "法律",
        "effective_date": "2019-08-26",
        "status": "有效",
        "category": "经济法",
        "description": "加强土地管理，保护耕地",
    },
    {
        "name": "中华人民共和国城市房地产管理法",
        "type": "法律",
        "effective_date": "2019-08-26",
        "status": "有效",
        "category": "经济法",
        "description": "规范城市房地产管理",
    },
    {
        "name": "中华人民共和国税收征收管理法",
        "type": "法律",
        "effective_date": "2015-04-24",
        "status": "有效",
        "category": "经济法",
        "description": "加强税收征收管理",
    },
    {
        "name": "中华人民共和国证券法",
        "type": "法律",
        "effective_date": "2020-03-01",
        "status": "有效",
        "category": "民法商法",
        "description": "规范证券发行和交易行为",
    },
    {
        "name": "中华人民共和国保险法",
        "type": "法律",
        "effective_date": "2015-04-24",
        "status": "有效",
        "category": "民法商法",
        "description": "规范保险活动，保护保险活动当事人的合法权益",
    },
    {
        "name": "中华人民共和国个人信息保护法",
        "type": "法律",
        "effective_date": "2021-11-01",
        "status": "有效",
        "category": "社会法",
        "description": "保护个人信息权益，规范个人信息处理活动",
    },
    {
        "name": "中华人民共和国数据安全法",
        "type": "法律",
        "effective_date": "2021-09-01",
        "status": "有效",
        "category": "社会法",
        "description": "规范数据处理活动，保障数据安全",
    },
]

# ---------------------------------------------------------------------------
# Seed data: key legal articles
# ---------------------------------------------------------------------------
KEY_ARTICLES = [
    # 宪法
    {"law_name": "中华人民共和国宪法", "article_number": "第二条", "title": "人民主权原则",
     "content": "中华人民共和国的一切权力属于人民。"},
    {"law_name": "中华人民共和国宪法", "article_number": "第五条", "title": "法治原则",
     "content": "中华人民共和国实行依法治国，建设社会主义法治国家。国家维护社会主义法制的统一和尊严。"},
    {"law_name": "中华人民共和国宪法", "article_number": "第三十三条", "title": "人权条款",
     "content": "国家尊重和保障人权。"},
    {"law_name": "中华人民共和国宪法", "article_number": "第一百三十五条", "title": "司法独立",
     "content": "人民法院、人民检察院和公安机关办理刑事案件，应当分工负责，互相配合，互相制约，以保证准确有效地执行法律。"},

    # 民法典
    {"law_name": "中华人民共和国民法典", "article_number": "第一条", "title": "立法目的",
     "content": "为了保护民事主体的合法权益，调整民事关系，维护社会和经济秩序，适应中国特色社会主义发展要求，弘扬社会主义核心价值观，根据宪法，制定本法。"},
    {"law_name": "中华人民共和国民法典", "article_number": "第三条", "title": "民事权益受法律保护",
     "content": "民事主体的人身权利、财产权利以及其他合法权益受法律保护，任何组织或者个人不得侵犯。"},
    {"law_name": "中华人民共和国民法典", "article_number": "第四条", "title": "平等原则",
     "content": "民事主体在民事活动中的法律地位一律平等。"},
    {"law_name": "中华人民共和国民法典", "article_number": "第七条", "title": "诚信原则",
     "content": "民事主体从事民事活动，应当遵循诚信原则，秉持诚实，恪守承诺。"},
    {"law_name": "中华人民共和国民法典", "article_number": "第八条", "title": "公序良俗原则",
     "content": "民事主体从事民事活动，不得违反法律，不得违背公序良俗。"},
    {"law_name": "中华人民共和国民法典", "article_number": "第一百一十九条", "title": "合同约束力",
     "content": "依法成立的合同，对当事人具有法律约束力。"},
    {"law_name": "中华人民共和国民法典", "article_number": "第一千零七十六条", "title": "协议离婚",
     "content": "夫妻双方自愿离婚的，应当签订书面离婚协议，并亲自到婚姻登记机关申请离婚登记。"},
    {"law_name": "中华人民共和国民法典", "article_number": "第一千一百六十五条", "title": "过错责任原则",
     "content": "行为人因过错侵害他人民事权益造成损害的，应当承担侵权责任。"},

    # 刑法
    {"law_name": "中华人民共和国刑法", "article_number": "第一条", "title": "立法目的",
     "content": "为了惩罚犯罪，保护人民，根据宪法，结合我国同犯罪作斗争的具体经验及实际情况，制定本法。"},
    {"law_name": "中华人民共和国刑法", "article_number": "第三条", "title": "罪刑法定原则",
     "content": "法律明文规定为犯罪行为的，依照法律定罪处刑；法律没有明文规定为犯罪行为的，不得定罪处刑。"},
    {"law_name": "中华人民共和国刑法", "article_number": "第四条", "title": "适用刑法人人平等原则",
     "content": "对任何人犯罪，在适用法律上一律平等。不允许任何人有超越法律的特权。"},
    {"law_name": "中华人民共和国刑法", "article_number": "第五条", "title": "罪责刑相适应原则",
     "content": "刑罚的轻重，应当与犯罪分子所犯罪行和承担的刑事责任相适应。"},
    {"law_name": "中华人民共和国刑法", "article_number": "第十三条", "title": "犯罪定义",
     "content": "一切危害国家主权、领土完整和安全，分裂国家、颠覆人民民主专政的政权和推翻社会主义制度，破坏社会秩序和经济秩序，侵犯国有财产或者劳动群众集体所有的财产，侵犯公民私人所有的财产，侵犯公民的人身权利、民主权利和其他权利，以及其他危害社会的行为，依照法律应当受刑罚处罚的，都是犯罪，但是情节显著轻微危害不大的，不认为是犯罪。"},
    {"law_name": "中华人民共和国刑法", "article_number": "第二十条", "title": "正当防卫",
     "content": "为了使国家、公共利益、本人或者他人的人身、财产和其他权利免受正在进行的不法侵害，而采取的制止不法侵害的行为，对不法侵害人造成损害的，属于正当防卫，不负刑事责任。"},
    {"law_name": "中华人民共和国刑法", "article_number": "第二十三条", "title": "犯罪未遂",
     "content": "已经着手实行犯罪，由于犯罪分子意志以外的原因而未得逞的，是犯罪未遂。"},

    # 刑事诉讼法
    {"law_name": "中华人民共和国刑事诉讼法", "article_number": "第十二条", "title": "无罪推定",
     "content": "未经人民法院依法判决，对任何人都不得确定有罪。"},
    {"law_name": "中华人民共和国刑事诉讼法", "article_number": "第五十条", "title": "证据裁判原则",
     "content": "审判人员、检察人员、侦查人员必须依照法定程序，收集能够证实犯罪嫌疑人、被告人有罪或者无罪、犯罪情节轻重的各种证据。"},

    # 民事诉讼法
    {"law_name": "中华人民共和国民事诉讼法", "article_number": "第八条", "title": "当事人平等原则",
     "content": "民事诉讼当事人有平等的诉讼权利。人民法院审理民事案件，应当保障和便利当事人行使诉讼权利，对当事人在适用法律上一律平等。"},
    {"law_name": "中华人民共和国民事诉讼法", "article_number": "第十三条", "title": "诚实信用原则",
     "content": "民事诉讼应当遵循诚实信用原则。"},

    # 公司法
    {"law_name": "中华人民共和国公司法", "article_number": "第三条", "title": "公司法人地位",
     "content": "公司是企业法人，有独立的法人财产，享有法人财产权。公司以其全部财产对公司的债务承担责任。"},
    {"law_name": "中华人民共和国公司法", "article_number": "第二十条", "title": "股东义务",
     "content": "公司股东应当遵守法律、行政法规和公司章程，依法行使股东权利，不得滥用股东权利损害公司或者其他股东的利益。"},

    # 劳动合同法
    {"law_name": "中华人民共和国劳动合同法", "article_number": "第三条", "title": "劳动合同原则",
     "content": "订立劳动合同，应当遵循合法、公平、平等自愿、协商一致、诚实信用的原则。"},
    {"law_name": "中华人民共和国劳动合同法", "article_number": "第十条", "title": "书面劳动合同",
     "content": "建立劳动关系，应当订立书面劳动合同。"},

    # 行政许可法
    {"law_name": "中华人民共和国行政许可法", "article_number": "第四条", "title": "法定原则",
     "content": "设定和实施行政许可，应当依照法定的权限、范围、条件和程序。"},

    # 行政处罚法
    {"law_name": "中华人民共和国行政处罚法", "article_number": "第四条", "title": "处罚法定原则",
     "content": "公民、法人或者其他组织违反行政管理秩序的行为，应当给予行政处罚的，依照本法由法律、法规、规章规定，并由行政机关依照本法规定的程序实施。"},

    # 个人信息保护法
    {"law_name": "中华人民共和国个人信息保护法", "article_number": "第五条", "title": "合法正当必要原则",
     "content": "处理个人信息应当遵循合法、正当、必要和诚信原则，不得通过误导、欺诈、胁迫等方式处理个人信息。"},
    {"law_name": "中华人民共和国个人信息保护法", "article_number": "第十三条", "title": "个人信息处理合法性基础",
     "content": "符合下列情形之一的，个人信息处理者方可处理个人信息：（一）取得个人的同意。"},

    # 数据安全法
    {"law_name": "中华人民共和国数据安全法", "article_number": "第三条", "title": "数据定义",
     "content": "本法所称数据，是指任何以电子或者其他方式对信息的记录。"},

    # 环境保护法
    {"law_name": "中华人民共和国环境保护法", "article_number": "第五条", "title": "环境保护基本原则",
     "content": "环境保护坚持保护优先、预防为主、综合治理、公众参与、损害担责的原则。"},

    # 证券法
    {"law_name": "中华人民共和国证券法", "article_number": "第三条", "title": "三公原则",
     "content": "证券的发行、交易活动，必须遵循公开、公平、公正的原则。"},

    # 反垄断法
    {"law_name": "中华人民共和国反垄断法", "article_number": "第三条", "title": "垄断行为",
     "content": "本法规定的垄断行为包括：（一）经营者达成垄断协议；（二）经营者滥用市场支配地位；（三）具有或者可能具有排除、限制竞争效果的经营者集中。"},

    # 立法法
    {"law_name": "中华人民共和国立法法", "article_number": "第三条", "title": "立法原则",
     "content": "立法应当遵循宪法的基本原则，不得与宪法相抵触。"},
]

# ---------------------------------------------------------------------------
# Seed data: legal concepts
# ---------------------------------------------------------------------------
LEGAL_CONCEPTS = [
    {"name": "法治原则", "category": "基本原则", "definition": "依法治国，建设社会主义法治国家，法律面前人人平等"},
    {"name": "罪刑法定", "category": "刑法原则", "definition": "法无明文规定不为罪，法无明文规定不处罚"},
    {"name": "无罪推定", "category": "诉讼法原则", "definition": "未经人民法院依法判决，对任何人都不得确定有罪"},
    {"name": "诚实信用", "category": "民法原则", "definition": "民事主体从事民事活动，应当遵循诚信原则，秉持诚实，恪守承诺"},
    {"name": "公序良俗", "category": "民法原则", "definition": "民事活动不得违反法律，不得违背公共秩序和善良风俗"},
    {"name": "正当防卫", "category": "刑法概念", "definition": "为使国家、公共利益、本人或他人的人身和其他权利免受正在进行的不法侵害，而采取的制止行为"},
    {"name": "紧急避险", "category": "刑法概念", "definition": "为了使合法权益免受正在发生的危险，不得已损害另一较小合法权益的行为"},
    {"name": "法人", "category": "民法概念", "definition": "具有民事权利能力和民事行为能力，依法独立享有民事权利和承担义务的组织"},
    {"name": "合同", "category": "民法概念", "definition": "民事主体之间设立、变更、终止民事法律关系的协议"},
    {"name": "侵权责任", "category": "民法概念", "definition": "行为人因过错侵害他人民事权益应当承担的法律责任"},
    {"name": "物权", "category": "民法概念", "definition": "权利人依法对特定的物享有直接支配和排他的权利"},
    {"name": "债权", "category": "民法概念", "definition": "权利人请求特定义务人为或者不为一定行为的权利"},
    {"name": "知识产权", "category": "民法概念", "definition": "权利人依法就作品、发明、商标等客体享有的专有的权利"},
    {"name": "行政处罚", "category": "行政法概念", "definition": "行政机关依法对违反行政管理秩序的公民、法人或者其他组织，以减损权益或者增加义务的方式予以惩戒的行为"},
    {"name": "行政许可", "category": "行政法概念", "definition": "行政机关根据公民、法人或者其他组织的申请，经依法审查，准予其从事特定活动的行为"},
    {"name": "行政诉讼", "category": "诉讼法概念", "definition": "公民、法人或者其他组织认为行政机关的行政行为侵犯其合法权益，依法向人民法院提起诉讼"},
    {"name": "管辖权", "category": "诉讼法概念", "definition": "人民法院之间受理第一审民事案件的权限和分工"},
    {"name": "犯罪构成", "category": "刑法概念", "definition": "刑法规定的成立犯罪所必须具备的主观要件和客观要件的总和"},
    {"name": "民事权利能力", "category": "民法概念", "definition": "民事主体依法享有民事权利、承担民事义务的资格"},
    {"name": "民事行为能力", "category": "民法概念", "definition": "民事主体能以自己的行为取得民事权利和承担民事义务的资格"},
    {"name": "诉讼时效", "category": "民法概念", "definition": "权利人在法定期间内不行使权利，义务人获得提出不履行义务的抗辩权的制度"},
    {"name": "劳动关系", "category": "劳动法概念", "definition": "用人单位与劳动者在实现劳动过程中建立的社会经济关系"},
    {"name": "个人信息", "category": "数据法概念", "definition": "以电子或者其他方式记录的与已识别或者可识别的自然人有关的各种信息"},
    {"name": "数据安全", "category": "数据法概念", "definition": "通过采取必要措施，确保数据处于有效保护和合法利用的状态"},
    {"name": "市场支配地位", "category": "经济法概念", "definition": "经营者在相关市场内具有能够控制商品价格、数量或者其他交易条件的能力"},
    {"name": "垄断协议", "category": "经济法概念", "definition": "经营者之间达成的排除、限制竞争的协议、决定或者其他协同行为"},
    {"name": "公司法人人格否认", "category": "商法概念", "definition": "在特定情形下否认公司独立人格，由股东对公司债务承担连带责任"},
    {"name": "善意取得", "category": "民法概念", "definition": "无权处分人将其占有的动产或登记在其名下的不动产转让给第三人，第三人在善意且支付合理对价时取得该财产的所有权"},
    {"name": "格式条款", "category": "民法概念", "definition": "当事人为了重复使用而预先拟定，并在订立合同时未与对方协商的条款"},
    {"name": "国家主权", "category": "宪法概念", "definition": "国家独立自主地处理自己内外事务的最高权力"},
]

# ---------------------------------------------------------------------------
# Seed data: court levels
# ---------------------------------------------------------------------------
COURTS = [
    {"name": "最高人民法院", "level": "最高", "type": "最高法院", "description": "国家最高审判机关，监督地方各级人民法院和专门人民法院的审判工作"},
    {"name": "高级人民法院", "level": "高级", "type": "高级法院", "description": "各省、自治区、直辖市设立的高级人民法院"},
    {"name": "中级人民法院", "level": "中级", "type": "中级法院", "description": "在省、自治区内按地区设立，在直辖市内设立，在省辖市设立"},
    {"name": "基层人民法院", "level": "基层", "type": "基层法院", "description": "县、县级市、市辖区设立的人民法院"},
    {"name": "最高人民法院知识产权法庭", "level": "专门", "type": "专门法院", "description": "统一审理全国范围内专利等技术类知识产权上诉案件"},
    {"name": "北京互联网法院", "level": "中级", "type": "互联网法院", "description": "集中管辖北京市辖区内应当由基层人民法院受理的第一审特定类型互联网案件"},
    {"name": "上海金融法院", "level": "中级", "type": "专门法院", "description": "管辖上海市辖区内应由中级人民法院受理的第一审金融民商事案件和涉金融行政案件"},
]

# ---------------------------------------------------------------------------
# Seed data: legal categories
# ---------------------------------------------------------------------------
LEGAL_CATEGORIES = [
    {"name": "宪法及宪法相关法", "description": "国家根本制度和基本法律制度的法律规范"},
    {"name": "民法商法", "description": "调整平等主体之间人身关系和财产关系的法律规范"},
    {"name": "行政法", "description": "调整行政关系的法律规范，规范行政权力的设定和行使"},
    {"name": "经济法", "description": "调整国家在经济管理中发生的经济关系的法律规范"},
    {"name": "社会法", "description": "调整劳动关系、社会保障和社会福利关系的法律规范"},
    {"name": "刑法", "description": "规定犯罪和刑罚的法律规范"},
    {"name": "诉讼与非诉讼程序法", "description": "规定诉讼程序和非诉讼程序的法律规范"},
]

# ---------------------------------------------------------------------------
# Cross-reference data (article-to-article references)
# ---------------------------------------------------------------------------
CROSS_REFERENCES = [
    # 民法典 references to 宪法
    {"from_law": "中华人民共和国民法典", "from_article": "第一条",
     "to_law": "中华人民共和国宪法", "to_article": "第五条"},
    # 刑法 principles referenced across
    {"from_law": "中华人民共和国刑法", "from_article": "第三条",
     "to_law": "中华人民共和国刑事诉讼法", "to_article": "第十二条"},
    # 公司法 references 民法典
    {"from_law": "中华人民共和国公司法", "from_article": "第三条",
     "to_law": "中华人民共和国民法典", "to_article": "第一百一十九条"},
    # 劳动合同法 references 民法典 (合同原则)
    {"from_law": "中华人民共和国劳动合同法", "from_article": "第三条",
     "to_law": "中华人民共和国民法典", "to_article": "第七条"},
    # 个人信息保护法 references 宪法 (人权条款)
    {"from_law": "中华人民共和国个人信息保护法", "from_article": "第五条",
     "to_law": "中华人民共和国宪法", "to_article": "第三十三条"},
    # 反垄断法 references 宪法 (经济秩序)
    {"from_law": "中华人民共和国反垄断法", "from_article": "第三条",
     "to_law": "中华人民共和国宪法", "to_article": "第五条"},
    # 证券法 references 公司法
    {"from_law": "中华人民共和国证券法", "from_article": "第三条",
     "to_law": "中华人民共和国公司法", "from_article_ref": "第三条"},
    # 环境保护法 references 宪法
    {"from_law": "中华人民共和国环境保护法", "from_article": "第五条",
     "to_law": "中华人民共和国宪法", "to_article": "第五条"},
    # 立法法 references 宪法
    {"from_law": "中华人民共和国立法法", "from_article": "第三条",
     "to_law": "中华人民共和国宪法", "to_article": "第五条"},
    # 行政许可法 references 行政处罚法 (程序衔接)
    {"from_law": "中华人民共和国行政许可法", "from_article": "第四条",
     "to_law": "中华人民共和国行政处罚法", "to_article": "第四条"},
]


def load_laws_from_json():
    """Load laws from the JSON dataset file."""
    if not LAWS_JSON_PATH.exists():
        print(f"[WARN] Laws dataset not found at {LAWS_JSON_PATH}")
        return []
    try:
        with open(LAWS_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"[INFO] Loaded {len(data)} laws from {LAWS_JSON_PATH.name}")
        return data
    except Exception as e:
        print(f"[WARN] Failed to load laws JSON: {e}")
        return []


def create_constraints(session):
    """Create uniqueness constraints and indexes in Neo4j."""
    constraints = [
        "CREATE CONSTRAINT law_name_unique IF NOT EXISTS FOR (n:Law) REQUIRE n.name IS UNIQUE",
        "CREATE CONSTRAINT article_id_unique IF NOT EXISTS FOR (n:Article) REQUIRE n.article_id IS UNIQUE",
        "CREATE CONSTRAINT concept_name_unique IF NOT EXISTS FOR (n:Concept) REQUIRE n.name IS UNIQUE",
        "CREATE CONSTRAINT court_name_unique IF NOT EXISTS FOR (n:Court) REQUIRE n.name IS UNIQUE",
        "CREATE CONSTRAINT category_name_unique IF NOT EXISTS FOR (n:LegalCategory) REQUIRE n.name IS UNIQUE",
    ]
    for c in constraints:
        try:
            session.run(c)
        except Exception as e:
            print(f"  [WARN] Constraint creation note: {e}")
    print("[INFO] Constraints and indexes created")


def seed_laws(session, laws_data):
    """Create Law nodes using UNWIND for batch performance."""
    # Use the ~31 major laws as seed data
    law_nodes = []
    for law in MAJOR_LAWS:
        law_nodes.append({
            "name": law["name"],
            "law_type": law["type"],
            "effective_date": law["effective_date"],
            "status": law["status"],
            "description": law.get("description", ""),
        })

    # Also pull top national-level laws from the JSON dataset
    seen_names = {l["name"] for l in law_nodes}
    national_types = {"宪法", "法律", "法律解释", "有关法律问题和重大问题的决定"}
    for entry in laws_data:
        if entry.get("type") in national_types and entry.get("title") not in seen_names:
            law_nodes.append({
                "name": entry["title"],
                "law_type": entry.get("type", ""),
                "effective_date": entry.get("publish", "")[:10] if entry.get("publish") else "",
                "status": entry.get("status", "有效"),
                "description": "",
            })
            seen_names.add(entry["title"])
            if len(law_nodes) >= 100:
                break

    query = """
        UNWIND $laws AS law
        MERGE (n:Law {name: law.name})
        SET n.type = law.law_type,
            n.effective_date = law.effective_date,
            n.status = law.status,
            n.description = law.description
    """
    result = session.run(query, laws=law_nodes)
    result.consume()
    print(f"[INFO] Created/merged {len(law_nodes)} Law nodes")
    return len(law_nodes)


def seed_legal_categories(session):
    """Create LegalCategory nodes."""
    query = """
        UNWIND $categories AS cat
        MERGE (n:LegalCategory {name: cat.name})
        SET n.description = cat.description
    """
    session.run(query, categories=LEGAL_CATEGORIES).consume()
    print(f"[INFO] Created/merged {len(LEGAL_CATEGORIES)} LegalCategory nodes")
    return len(LEGAL_CATEGORIES)


def seed_articles(session):
    """Create Article nodes and link them to Law nodes."""
    article_nodes = []
    for art in KEY_ARTICLES:
        article_id = f"{art['law_name']}_{art['article_number']}"
        article_nodes.append({
            "article_id": article_id,
            "law_name": art["law_name"],
            "article_number": art["article_number"],
            "title": art.get("title", ""),
            "content": art.get("content", ""),
        })

    # Create Article nodes
    query_nodes = """
        UNWIND $articles AS art
        MERGE (n:Article {article_id: art.article_id})
        SET n.law_name = art.law_name,
            n.article_number = art.article_number,
            n.title = art.title,
            n.content = art.content
    """
    session.run(query_nodes, articles=article_nodes).consume()
    print(f"[INFO] Created/merged {len(article_nodes)} Article nodes")

    # Create CONTAINS relationships: Law -> Article
    query_contains = """
        UNWIND $articles AS art
        MATCH (law:Law {name: art.law_name})
        MATCH (article:Article {article_id: art.article_id})
        MERGE (law)-[:CONTAINS]->(article)
    """
    session.run(query_contains, articles=article_nodes).consume()
    print(f"[INFO] Created CONTAINS relationships (Law -> Article)")

    return len(article_nodes)


def seed_concepts(session):
    """Create Concept nodes."""
    query = """
        UNWIND $concepts AS concept
        MERGE (n:Concept {name: concept.name})
        SET n.category = concept.category,
            n.definition = concept.definition
    """
    session.run(query, concepts=LEGAL_CONCEPTS).consume()
    print(f"[INFO] Created/merged {len(LEGAL_CONCEPTS)} Concept nodes")
    return len(LEGAL_CONCEPTS)


def seed_courts(session):
    """Create Court nodes."""
    query = """
        UNWIND $courts AS court
        MERGE (n:Court {name: court.name})
        SET n.level = court.level,
            n.type = court.type,
            n.description = court.description
    """
    session.run(query, courts=COURTS).consume()
    print(f"[INFO] Created/merged {len(COURTS)} Court nodes")
    return len(COURTS)


def create_law_category_relationships(session):
    """Create CATEGORY relationships: Law -> LegalCategory."""
    category_mapping = {}
    for law in MAJOR_LAWS:
        category_mapping[law["name"]] = law["category"]

    pairs = [
        {"law_name": name, "category": cat}
        for name, cat in category_mapping.items()
    ]

    query = """
        UNWIND $pairs AS pair
        MATCH (law:Law {name: pair.law_name})
        MATCH (cat:LegalCategory {name: pair.category})
        MERGE (law)-[:CATEGORY]->(cat)
    """
    session.run(query, pairs=pairs).consume()
    print(f"[INFO] Created CATEGORY relationships (Law -> LegalCategory)")


def create_concept_relationships(session):
    """Create CONCEPT relationships linking Articles to Concepts based on domain knowledge."""
    # Map articles to concepts based on their legal domain
    article_concept_pairs = [
        # 宪法 articles -> constitutional concepts
        ("中华人民共和国宪法_第二条", "国家主权"),
        ("中华人民共和国宪法_第五条", "法治原则"),
        ("中华人民共和国宪法_第三十三条", "法治原则"),
        # 民法典 articles -> civil law concepts
        ("中华人民共和国民法典_第三条", "民事权利能力"),
        ("中华人民共和国民法典_第四条", "法人"),
        ("中华人民共和国民法典_第七条", "诚实信用"),
        ("中华人民共和国民法典_第八条", "公序良俗"),
        ("中华人民共和国民法典_第一百一十九条", "合同"),
        ("中华人民共和国民法典_第一千零七十六条", "合同"),
        ("中华人民共和国民法典_第一千一百六十五条", "侵权责任"),
        # 刑法 articles -> criminal law concepts
        ("中华人民共和国刑法_第三条", "罪刑法定"),
        ("中华人民共和国刑法_第十三条", "犯罪构成"),
        ("中华人民共和国刑法_第二十条", "正当防卫"),
        ("中华人民共和国刑法_第二十三条", "犯罪构成"),
        # 刑事诉讼法 articles -> procedural concepts
        ("中华人民共和国刑事诉讼法_第十二条", "无罪推定"),
        ("中华人民共和国刑事诉讼法_第五十条", "管辖权"),
        # 民事诉讼法 articles
        ("中华人民共和国民事诉讼法_第八条", "管辖权"),
        ("中华人民共和国民事诉讼法_第十三条", "诚实信用"),
        # 公司法 articles
        ("中华人民共和国公司法_第三条", "法人"),
        ("中华人民共和国公司法_第二十条", "公司法人人格否认"),
        # 劳动合同法 articles
        ("中华人民共和国劳动合同法_第三条", "劳动关系"),
        ("中华人民共和国劳动合同法_第十条", "劳动关系"),
        # 个人信息保护法 articles
        ("中华人民共和国个人信息保护法_第五条", "个人信息"),
        ("中华人民共和国个人信息保护法_第十三条", "个人信息"),
        # 数据安全法 articles
        ("中华人民共和国数据安全法_第三条", "数据安全"),
        # 反垄断法 articles
        ("中华人民共和国反垄断法_第三条", "垄断协议"),
        # 证券法 articles
        ("中华人民共和国证券法_第三条", "法人"),
    ]

    pairs = [
        {"article_id": art_id, "concept_name": concept}
        for art_id, concept in article_concept_pairs
    ]

    query = """
        UNWIND $pairs AS pair
        MATCH (art:Article {article_id: pair.article_id})
        MATCH (concept:Concept {name: pair.concept_name})
        MERGE (art)-[:CONCEPT]->(concept)
    """
    session.run(query, pairs=pairs).consume()
    print(f"[INFO] Created CONCEPT relationships (Article -> Concept): {len(pairs)} links")
    return len(pairs)


def create_cross_references(session):
    """Create REFERENCES relationships between articles."""
    ref_pairs = []
    for ref in CROSS_REFERENCES:
        from_id = f"{ref['from_law']}_{ref['from_article']}"
        to_law = ref.get("to_law", "")
        to_article = ref.get("to_article", "")
        to_id = f"{to_law}_{to_article}"
        ref_pairs.append({"from_id": from_id, "to_id": to_id})

    query = """
        UNWIND $refs AS ref
        MATCH (from_art:Article {article_id: ref.from_id})
        MATCH (to_art:Article {article_id: ref.to_id})
        MERGE (from_art)-[:REFERENCES]->(to_art)
    """
    session.run(query, refs=ref_pairs).consume()
    print(f"[INFO] Created REFERENCES relationships (Article -> Article): {len(ref_pairs)} links")
    return len(ref_pairs)


def print_summary(session):
    """Print summary statistics of the seeded graph."""
    print("\n" + "=" * 60)
    print("  NEO4J KNOWLEDGE GRAPH - SEED SUMMARY")
    print("=" * 60)

    # Count nodes by label
    node_query = """
        MATCH (n)
        RETURN labels(n)[0] AS label, count(n) AS count
        ORDER BY count DESC
    """
    result = session.run(node_query)
    print("\n  Node counts:")
    total_nodes = 0
    for record in result:
        label = record["label"] or "Unknown"
        count = record["count"]
        total_nodes += count
        print(f"    {label:20s} : {count}")
    print(f"    {'TOTAL':20s} : {total_nodes}")

    # Count relationships by type
    rel_query = """
        MATCH ()-[r]->()
        RETURN type(r) AS type, count(r) AS count
        ORDER BY count DESC
    """
    result = session.run(rel_query)
    print("\n  Relationship counts:")
    total_rels = 0
    for record in result:
        rel_type = record["type"] or "Unknown"
        count = record["count"]
        total_rels += count
        print(f"    {rel_type:20s} : {count}")
    print(f"    {'TOTAL':20s} : {total_rels}")

    print(f"\n  Total nodes:          {total_nodes}")
    print(f"  Total relationships:  {total_rels}")
    print("=" * 60)


def main():
    """Main entry point for seeding the Neo4j knowledge graph."""
    print("=" * 60)
    print("  SEED NEO4J KNOWLEDGE GRAPH - Chinese Legal Knowledge")
    print("=" * 60)
    print(f"\n  Connecting to: {NEO4J_URI}")
    print(f"  User:          {NEO4J_USER}")
    print(f"  Timestamp:     {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # Connect to Neo4j
    try:
        driver = GraphDatabase.driver(
            NEO4J_URI,
            auth=(NEO4J_USER, NEO4J_PASSWORD),
        )
        # Verify connection
        with driver.session() as session:
            session.run("RETURN 1").consume()
        print("[OK] Connected to Neo4j successfully\n")
    except Exception as e:
        print(f"[ERROR] Failed to connect to Neo4j at {NEO4J_URI}")
        print(f"        {e}")
        print("\n  Troubleshooting:")
        print("  1. Ensure Neo4j is running:  docker ps | grep neo4j")
        print("  2. Check credentials in this script match your Neo4j setup")
        print("  3. Verify the URI is reachable from this machine")
        sys.exit(1)

    try:
        with driver.session() as session:
            # Step 1: Create constraints and indexes
            print("[STEP 1] Creating constraints and indexes...")
            create_constraints(session)

            # Step 2: Load laws from JSON dataset
            print("\n[STEP 2] Loading law data from dataset...")
            laws_data = load_laws_from_json()

            # Step 3: Seed Law nodes
            print("\n[STEP 3] Seeding Law nodes...")
            law_count = seed_laws(session, laws_data)

            # Step 4: Seed LegalCategory nodes
            print("\n[STEP 4] Seeding LegalCategory nodes...")
            cat_count = seed_legal_categories(session)

            # Step 5: Seed Article nodes and CONTAINS relationships
            print("\n[STEP 5] Seeding Article nodes...")
            art_count = seed_articles(session)

            # Step 6: Seed Concept nodes
            print("\n[STEP 6] Seeding Concept nodes...")
            concept_count = seed_concepts(session)

            # Step 7: Seed Court nodes
            print("\n[STEP 7] Seeding Court nodes...")
            court_count = seed_courts(session)

            # Step 8: Create CATEGORY relationships
            print("\n[STEP 8] Creating Law -> LegalCategory relationships...")
            create_law_category_relationships(session)

            # Step 9: Create CONCEPT relationships
            print("\n[STEP 9] Creating Article -> Concept relationships...")
            concept_rel_count = create_concept_relationships(session)

            # Step 10: Create cross-reference relationships
            print("\n[STEP 10] Creating Article -> Article REFERENCES relationships...")
            ref_count = create_cross_references(session)

            # Step 11: Print summary
            print_summary(session)

        print("\n[DONE] Knowledge graph seeding completed successfully!")

    except Exception as e:
        print(f"\n[ERROR] Seeding failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        driver.close()
        print("\n[INFO] Neo4j driver closed.")


if __name__ == "__main__":
    main()
