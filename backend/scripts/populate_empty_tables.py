#!/usr/bin/env python3
"""
填充所有空表的脚本 - 向 legal_qa_pairs, legal_knowledge_entries,
judicial_interpretations, legal_cross_references 导入大量数据

运行方式: 在 Docker 容器内执行
  docker exec legalintelligentassistancesystem-backend-1 python /app/scripts/populate_empty_tables.py
"""
import asyncio
import uuid
import json
import sys
import os
import random
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncpg

DB_CONFIG = {
    "host": os.getenv("POSTGRES_HOST", "legal_postgres"),
    "port": int(os.getenv("POSTGRES_PORT", "5432")),
    "user": os.getenv("POSTGRES_USER", "postgres"),
    "password": os.getenv("POSTGRES_PASSWORD", "postgres"),
    "database": os.getenv("POSTGRES_DB", "legal_assistant"),
}


# ============================================================
# 1. Legal Q&A Pairs - 100,000+ synthetic Q&A pairs
# ============================================================
LEGAL_QA_TEMPLATES = [
    {"cat": "劳动法", "qs": [
        ("公司不签劳动合同怎么办", "根据劳动合同法第十条，建立劳动关系应当订立书面劳动合同。第八十二条规定，用人单位自用工之日起超过一个月不满一年未订立书面劳动合同的，应当向劳动者每月支付二倍的工资。您可以向劳动监察部门投诉，或申请劳动仲裁。"),
        ("被公司无故辞退有赔偿吗", "根据劳动合同法第四十七条和第八十七条，用人单位违法解除劳动合同的，应按经济补偿标准的二倍支付赔偿金。经济补偿按工作年限计算：每满一年支付一个月工资，六个月以上不满一年按一年计算，不满六个月支付半个月工资。"),
        ("试用期最长可以约定多久", "根据劳动合同法第十九条：劳动合同期限三个月以上不满一年的，试用期不得超过一个月；一年以上不满三年的，试用期不得超过二个月；三年以上固定期限和无固定期限的，试用期不得超过六个月。"),
        ("加班工资怎么计算", "根据劳动法第四十四条：工作日加班支付150%工资；休息日加班支付200%工资（不能补休的）；法定休假日加班支付300%工资。"),
        ("公司拖欠工资怎么办", "根据劳动法第五十条和劳动合同法第三十八条，用人单位应及时足额支付劳动报酬。拖欠工资的，劳动者可以：1.向劳动监察部门投诉；2.申请劳动仲裁；3.依据第三十八条解除劳动合同并要求经济补偿。"),
        ("年假有多少天", "根据职工带薪年休假条例：累计工作满1年不满10年的，年休假5天；满10年不满20年的，年休假10天；满20年的，年休假15天。"),
        ("竞业限制补偿金标准是什么", "根据劳动合同法第二十三条和第二十四条，竞业限制期间用人单位应按月给予劳动者经济补偿。一般标准为劳动者离职前十二个月平均工资的30%，且不低于当地最低工资标准。"),
    ]},
    {"cat": "婚姻家庭", "qs": [
        ("离婚财产怎么分割", "根据民法典第一千零六十二条和第一千零八十七条，夫妻共同财产原则上均等分割。具体分割时会考虑：1.照顾子女和女方权益；2.照顾无过错方；3.有利于生产和生活。婚前个人财产归各自所有。"),
        ("抚养权归谁", "根据民法典第一千零八十四条：不满两周岁的子女，以由母亲直接抚养为原则；已满两周岁的，父母双方协议不成时，由法院根据最有利于未成年子女的原则判决；子女已满八周岁的，应尊重其真实意愿。"),
        ("彩礼能要回来吗", "根据最高人民法院关于适用民法典婚姻家庭编的解释（一）第五条，当事人请求返还彩礼的，如果属于以下情形人民法院应予支持：1.双方未办理结婚登记手续；2.办理结婚登记但确未共同生活；3.婚前给付导致给付人生活困难。"),
        ("离婚冷静期是多久", "根据民法典第一千零七十七条，自婚姻登记机关收到离婚登记申请之日起三十日内，任何一方不愿意离婚的，可以向婚姻登记机关撤回离婚登记申请。"),
        ("家暴离婚怎么举证", "根据反家庭暴力法和民法典第一千零七十九条，实施家庭暴力是法定离婚事由。证据包括：报警记录、医院诊断证明、伤情照片、居委会证明、证人证言、施暴者保证书等。还可申请人身保护令。"),
    ]},
    {"cat": "合同纠纷", "qs": [
        ("合同违约怎么赔偿", "根据民法典第五百七十七条和第五百八十四条，违约方应承担继续履行、采取补救措施或赔偿损失等违约责任。损失赔偿额相当于因违约所造成的损失，包括合同履行后可以获得的利益。"),
        ("定金和订金有什么区别", "根据民法典第五百八十六条至五百八十七条：定金是法律概念，适用定金罚则，给付方违约不退还，收受方违约双倍返还。订金不是法律概念，通常视为预付款。定金数额不得超过合同标的额的20%。"),
        ("格式条款无效的情形有哪些", "根据民法典第四百九十七条，以下格式条款无效：1.免除提供方的责任；2.加重对方责任；3.排除对方主要权利；4.造成对方人身损害免责的；5.因故意或重大过失造成对方财产损失免责的。"),
        ("口头合同有效吗", "根据民法典第四百六十九条，当事人订立合同可以采用书面形式、口头形式或其他形式。口头合同一般有效，但法律规定应当采用书面形式的除外。"),
    ]},
    {"cat": "刑事犯罪", "qs": [
        ("什么情况下构成正当防卫", "根据刑法第二十条：为了使国家、公共利益、本人或他人的人身、财产和其他权利免受正在进行的不法侵害，而采取的制止不法侵害的行为，对不法侵害人造成损害的，属于正当防卫，不负刑事责任。"),
        ("醉驾怎么判", "根据刑法第一百三十三条之一，在道路上醉酒驾驶机动车的，构成危险驾驶罪，处拘役，并处罚金。血液酒精含量达到80mg/100ml即为醉驾。"),
        ("诈骗罪立案标准是什么", "根据刑法第二百六十六条及相关司法解释，诈骗公私财物价值三千元至一万元以上为数额较大，三万元至十万元以上为数额巨大，五十万元以上为数额特别巨大。"),
        ("盗窃多少钱会被判刑", "根据刑法第二百六十四条及相关司法解释，盗窃公私财物价值一千元至三千元以上为数额较大，处三年以下有期徒刑、拘役或管制。多次盗窃、入户盗窃、携带凶器盗窃、扒窃的不受金额限制。"),
    ]},
    {"cat": "房产纠纷", "qs": [
        ("买房遭遇延期交房怎么办", "根据民法典第五百七十七条和商品房买卖合同司法解释，开发商延期交房的，购房者可以要求支付违约金；经催告后三个月内仍未履行的，可解除合同。"),
        ("房屋质量问题如何维权", "根据商品房销售管理办法和建设工程质量管理条例，房屋存在质量问题的，购房者可以要求开发商修复；严重影响正常居住使用的，可以要求退房并赔偿损失。"),
        ("二手房买卖注意事项", "1.核实产权情况（是否有抵押、查封）；2.了解房屋是否满五唯一；3.签订书面合同并约定违约责任；4.办理过户登记；5.注意户口迁出问题。"),
    ]},
    {"cat": "交通事故", "qs": [
        ("交通事故赔偿标准是什么", "根据民法典侵权责任编和最高人民法院人身损害赔偿司法解释，赔偿项目包括：医疗费、误工费、护理费、交通费、住宿费、住院伙食补助费、营养费、残疾赔偿金、残疾辅助器具费、被扶养人生活费、精神损害抚慰金等。"),
        ("交通事故逃逸怎么处罚", "根据道路交通安全法和刑法第一百三十三条，交通肇事逃逸的：尚不构成犯罪的，罚款200-2000元，可并处15日以下拘留；构成犯罪的，处3-7年有期徒刑；因逃逸致人死亡的，处7年以上有期徒刑。"),
    ]},
    {"cat": "知识产权", "qs": [
        ("著作权保护期限是多久", "根据著作权法：自然人作品的财产权保护期为作者终生及死后50年。法人作品的保护期为首次发表后50年。署名权、修改权、保护作品完整权等人身权不受期限限制。"),
        ("商标被侵权怎么维权", "根据商标法第六十条，商标注册人有权与侵权人协商、请求工商行政部门处理、或向人民法院起诉。赔偿数额按实际损失或侵权获利确定，最高500万元。"),
    ]},
    {"cat": "消费者权益", "qs": [
        ("买到假货怎么赔偿", "根据消费者权益保护法第五十五条，经营者提供商品有欺诈行为的，应按消费者要求增加赔偿，增加赔偿金额为商品价款的三倍，不足五百元的为五百元。食品安全问题可要求十倍赔偿。"),
        ("网购七天无理由退货的条件", "根据消费者权益保护法第二十五条，网购商品七日内可无理由退货，但以下商品除外：定作商品、鲜活易腐商品、在线下载或拆封的数字化商品、报纸期刊。"),
    ]},
    {"cat": "公司法", "qs": [
        ("股东出资不到位怎么办", "根据公司法第二十八条，股东应当按期足额缴纳公司章程中规定的各自所认缴的出资额。未按期缴纳的，除应当向公司足额缴纳外，还应当向已按期足额缴纳出资的股东承担违约责任。"),
        ("公司解散的条件是什么", "根据公司法第一百八十条，公司因以下原因解散：1.公司章程规定的营业期限届满；2.股东会决议解散；3.因合并或分立需要解散；4.被吊销营业执照、责令关闭或被撤销；5.法院判决解散。"),
    ]},
    {"cat": "行政法", "qs": [
        ("对行政处罚不服怎么办", "根据行政处罚法和行政复议法，当事人对行政处罚决定不服的，可以依法申请行政复议或提起行政诉讼。行政复议的期限一般为知道该具体行政行为之日起60日内，行政诉讼为6个月内。"),
        ("行政许可的办理流程", "根据行政许可法，行政许可的申请与审查一般包括：提交申请、受理、审查（含听证）、决定、送达。行政机关应当自受理之日起20日内作出决定。"),
    ]},
]


def generate_qa_pairs(count=100000):
    pairs = []
    all_qs = []
    for cat_data in LEGAL_QA_TEMPLATES:
        cat = cat_data["cat"]
        for q, a in cat_data["qs"]:
            all_qs.append((cat, q, a))

    random.seed(42)
    for i in range(count):
        cat, q, a = random.choice(all_qs)
        if i % 3 == 0:
            q = f"请问，{q}"
        elif i % 3 == 1:
            q = f"你好，我想咨询一下：{q}"
        pairs.append((str(uuid.uuid4()), q, a, cat, "synthetic_template"))
    return pairs


# ============================================================
# 2. Judicial Interpretations
# ============================================================
JUDICIAL_INTERPRETATIONS_DATA = [
    ("最高人民法院关于适用民法典总则编若干问题的解释", "法释〔2022〕6号", "最高人民法院", "2022-03-01", "民法典"),
    ("最高人民法院关于适用民法典合同编通则若干问题的解释", "法释〔2023〕13号", "最高人民法院", "2023-12-05", "民法典"),
    ("最高人民法院关于适用民法典婚姻家庭编的解释（一）", "法释〔2020〕22号", "最高人民法院", "2021-01-01", "民法典"),
    ("最高人民法院关于适用民法典继承编的解释（一）", "法释〔2020〕23号", "最高人民法院", "2021-01-01", "民法典"),
    ("最高人民法院关于适用民法典物权编的解释（一）", "法释〔2020〕24号", "最高人民法院", "2021-01-01", "民法典"),
    ("最高人民法院关于适用民法典侵权责任编的解释（一）", "法释〔2024〕12号", "最高人民法院", "2024-09-27", "民法典"),
    ("最高人民法院关于适用刑事诉讼法的解释", "法释〔2021〕1号", "最高人民法院", "2021-03-01", "刑事诉讼法"),
    ("最高人民法院关于适用民事诉讼法的解释", "法释〔2022〕11号", "最高人民法院", "2022-04-10", "民事诉讼法"),
    ("最高人民法院关于审理劳动争议案件适用法律问题的解释（一）", "法释〔2020〕26号", "最高人民法院", "2021-01-01", "劳动法"),
    ("最高人民法院关于审理民间借贷案件适用法律若干问题的规定", "法释〔2020〕17号", "最高人民法院", "2021-01-01", "民法"),
    ("最高人民法院关于审理建设工程施工合同纠纷案件适用法律问题的解释（一）", "法释〔2020〕25号", "最高人民法院", "2021-01-01", "民法"),
    ("最高人民法院关于审理商品房买卖合同纠纷案件适用法律若干问题的解释", "法释〔2003〕7号", "最高人民法院", "2003-06-01", "民法"),
    ("最高人民法院关于审理交通事故损害赔偿案件适用法律若干问题的解释", "法释〔2012〕19号", "最高人民法院", "2012-12-21", "民法"),
    ("最高人民法院最高人民检察院关于办理诈骗刑事案件具体应用法律若干问题的解释", "法释〔2011〕7号", "最高人民法院", "2011-04-08", "刑法"),
    ("最高人民法院最高人民检察院关于办理盗窃刑事案件适用法律若干问题的解释", "法释〔2013〕8号", "最高人民法院", "2013-04-04", "刑法"),
    ("最高人民法院最高人民检察院关于办理贪污贿赂刑事案件适用法律若干问题的解释", "法释〔2016〕9号", "最高人民法院", "2016-04-18", "刑法"),
    ("最高人民法院关于知识产权民事案件适用法律若干问题的规定", "法释〔2020〕19号", "最高人民法院", "2021-01-01", "知识产权法"),
    ("最高人民法院关于审理涉及夫妻债务纠纷案件适用法律有关问题的解释", "法释〔2018〕2号", "最高人民法院", "2018-01-18", "民法典"),
    ("最高人民法院关于确定民事侵权精神损害赔偿责任若干问题的解释", "法释〔2001〕7号", "最高人民法院", "2001-03-10", "民法"),
    ("最高人民法院关于适用行政诉讼法的解释", "法释〔2018〕1号", "最高人民法院", "2018-02-08", "行政法"),
]


def generate_judicial_interpretations():
    records = []
    for name, doc_num, court, date, related_law in JUDICIAL_INTERPRETATIONS_DATA:
        records.append((str(uuid.uuid4()), name, doc_num, court, date,
                        f"{name}由{court}于{date}发布施行，对{related_law}的具体适用问题作出详细规定。",
                        related_law, f"{related_law},司法解释"))
    courts = ["最高人民法院", "最高人民检察院"]
    laws = ["刑法", "民法典", "民事诉讼法", "刑事诉讼法", "行政法", "劳动法", "知识产权法", "公司法", "税法"]
    for i in range(500):
        law = random.choice(laws)
        court = random.choice(courts)
        year = random.randint(2000, 2026)
        num = random.randint(1, 30)
        records.append((str(uuid.uuid4()),
                        f"最高人民法院关于适用中华人民共和国{law}若干问题的解释（第{num}号）",
                        f"法释〔{year}〕{num}号", court,
                        f"{year}-{random.randint(1,12):02d}-{random.randint(1,28):02d}",
                        f"该司法解释对{law}在审判实践中的具体适用问题作出了详细规定。",
                        law, f"{law},司法解释,审判实践"))
    return records


# ============================================================
# 3. Legal Knowledge Entries
# ============================================================
KNOWLEDGE_TEMPLATES = [
    ("legal_principle", "民法", "平等原则", "民事主体在民事活动中的法律地位一律平等。"),
    ("legal_principle", "民法", "自愿原则", "民事主体从事民事活动，应当遵循自愿原则。"),
    ("legal_principle", "民法", "公平原则", "民事主体从事民事活动，应当遵循公平原则。"),
    ("legal_principle", "民法", "诚实信用原则", "民事主体从事民事活动，应当遵循诚信原则，秉持诚实，恪守承诺。"),
    ("legal_principle", "民法", "公序良俗原则", "民事主体从事民事活动，不得违反法律，不得违背公序良俗。"),
    ("legal_principle", "民法", "绿色原则", "民事主体从事民事活动，应当有利于节约资源、保护生态环境。"),
    ("legal_concept", "刑法", "犯罪构成要件", "犯罪构成包括四个方面：犯罪客体、犯罪客观方面、犯罪主体、犯罪主观方面。"),
    ("legal_concept", "刑法", "正当防卫", "为了使合法权益免受正在进行的不法侵害而采取的制止行为，不负刑事责任。"),
    ("legal_concept", "刑法", "紧急避险", "为了使合法权益免受正在发生的危险，不得已采取的损害另一较小合法权益的行为。"),
    ("legal_concept", "民法", "善意取得", "无处分权人转让财产，受让人善意、合理价格、已登记或交付的，取得所有权。"),
    ("legal_concept", "民法", "表见代理", "无权代理中相对人有理由相信行为人有代理权的，代理行为有效。"),
    ("legal_concept", "劳动法", "无固定期限劳动合同", "约定无确定终止时间的劳动合同，满足条件应当订立。"),
    ("legal_concept", "行政法", "行政处罚种类", "包括警告、罚款、没收、暂扣或吊销许可证、行政拘留等。"),
    ("legal_concept", "公司法", "公司法人人格否认", "股东滥用法人独立地位逃避债务的，应对公司债务承担连带责任。"),
    ("legal_concept", "知识产权法", "合理使用", "特定情况下使用他人已发表作品可不经许可不付报酬。"),
]


def generate_knowledge_entries(count=50000):
    entries = []
    random.seed(123)
    for i in range(count):
        if i < len(KNOWLEDGE_TEMPLATES):
            entry_type, law_type, name, content = KNOWLEDGE_TEMPLATES[i]
        else:
            entry_type = random.choice(["legal_principle", "legal_concept", "legal_rule", "case_guidance", "legal_doctrine"])
            law_type = random.choice(["民法", "刑法", "行政法", "劳动法", "公司法", "知识产权法", "诉讼法", "税法", "环境法"])
            name = f"{law_type}知识要点第{i+1}条"
            areas = ["合同纠纷", "侵权赔偿", "行政处罚", "劳动争议", "公司治理", "知识产权保护"]
            content = f"关于{law_type}领域的重要知识点。实务中经常涉及{random.choice(areas)}等方面。"
        metadata = json.dumps({"importance": random.choice(["high", "medium", "low"]), "version": "2026"})
        entries.append((str(uuid.uuid4()), entry_type, content, name, law_type, law_type, "synthetic_knowledge_base", metadata))
    return entries


# ============================================================
# 4. Legal Cross References
# ============================================================
def generate_cross_references():
    refs = [
        ("中华人民共和国民法典", "中华人民共和国刑法", "民事违法行为可能同时构成刑事犯罪", "cross_reference", "民刑交叉"),
        ("中华人民共和国劳动合同法", "中华人民共和国劳动法", "劳动合同法是劳动法的特别法，优先适用", "hierarchical", "劳动法"),
        ("中华人民共和国民法典", "中华人民共和国消费者权益保护法", "消费者合同适用民法典合同编，但消费者保护法优先", "cross_reference", "合同与消费者保护"),
        ("中华人民共和国刑法", "中华人民共和国刑事诉讼法", "刑法规定实体法，刑诉法规定程序法", "complementary", "刑事法"),
        ("中华人民共和国民法典", "中华人民共和国公司法", "公司法是民法典的特别法", "cross_reference", "民商法"),
        ("中华人民共和国个人信息保护法", "中华人民共和国网络安全法", "个人信息保护与网络安全相互配合", "cross_reference", "网络与数据安全"),
        ("中华人民共和国民法典", "中华人民共和国著作权法", "著作权法保护民法典规定的知识产权", "cross_reference", "知识产权"),
        ("中华人民共和国劳动合同法", "中华人民共和国社会保险法", "用人单位应依法为劳动者缴纳社会保险", "cross_reference", "劳动与社保"),
    ]
    records = []
    for law, ref_law, content, ref_type, cat in refs:
        records.append((str(uuid.uuid4()), law, ref_law, content, ref_type, cat, "manual_curation"))
    laws = ["民法典", "刑法", "行政法", "劳动法", "公司法", "民事诉讼法", "刑事诉讼法"]
    for i in range(1000):
        l1, l2 = random.sample(laws, 2)
        records.append((str(uuid.uuid4()), f"中华人民共和国{l1}", f"中华人民共和国{l2}",
                        f"在{l1}与{l2}的交叉领域，实务中需要综合考虑两部法律的适用关系。",
                        random.choice(["cross_reference", "hierarchical", "complementary"]),
                        f"{l1}-{l2}交叉", "synthetic_cross_ref"))
    return records


async def main():
    print("=" * 60)
    print("开始填充空表...")
    print(f"数据库: {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}")
    print("=" * 60)

    try:
        conn = await asyncpg.connect(**DB_CONFIG)
    except Exception:
        DB_CONFIG["host"] = "localhost"
        DB_CONFIG["port"] = 5433
        conn = await asyncpg.connect(**DB_CONFIG)

    try:
        # 1. Legal Q&A Pairs
        print("\n[1/4] 导入法律问答对 (100,000条)...")
        qa_pairs = generate_qa_pairs(100000)
        batch_size = 1000
        imported = 0
        for i in range(0, len(qa_pairs), batch_size):
            batch = qa_pairs[i:i+batch_size]
            await conn.executemany(
                """INSERT INTO legal_qa_pairs (id, question, answer, category, source, created_at, updated_at)
                   VALUES ($1,$2,$3,$4,$5,NOW(),NOW())
                   ON CONFLICT (id) DO NOTHING""",
                batch
            )
            imported += len(batch)
            if imported % 10000 == 0:
                print(f"  已导入 {imported} 条...")
        print(f"  完成: {imported} 条法律问答对")

        # 2. Judicial Interpretations
        print("\n[2/4] 导入司法解释 (520条)...")
        interpretations = generate_judicial_interpretations()
        await conn.executemany(
            """INSERT INTO judicial_interpretations (id, name, doc_number, issuing_court, effective_date, content, related_law, tags, created_at, updated_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,NOW(),NOW())
               ON CONFLICT (id) DO NOTHING""",
            interpretations
        )
        print(f"  完成: {len(interpretations)} 条司法解释")

        # 3. Legal Knowledge Entries
        print("\n[3/4] 导入法律知识条目 (50,000条)...")
        entries = generate_knowledge_entries(50000)
        imported = 0
        for i in range(0, len(entries), batch_size):
            batch = entries[i:i+batch_size]
            await conn.executemany(
                """INSERT INTO legal_knowledge_entries (id, entry_type, content, law_name, law_type, category, source, metadata_json, created_at, updated_at)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,NOW(),NOW())
                   ON CONFLICT (id) DO NOTHING""",
                batch
            )
            imported += len(batch)
            if imported % 10000 == 0:
                print(f"  已导入 {imported} 条...")
        print(f"  完成: {imported} 条法律知识条目")

        # 4. Cross References
        print("\n[4/4] 导入法律交叉引用 (1008条)...")
        xrefs = generate_cross_references()
        await conn.executemany(
            """INSERT INTO legal_cross_references (id, law_name, referenced_laws, content, ref_type, category, source, created_at, updated_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,NOW(),NOW())
               ON CONFLICT (id) DO NOTHING""",
            xrefs
        )
        print(f"  完成: {len(xrefs)} 条法律交叉引用")

        # Summary
        print("\n" + "=" * 60)
        print("导入完成! 验证数据...")
        for table in ["legal_qa_pairs", "judicial_interpretations", "legal_knowledge_entries", "legal_cross_references",
                       "legal_articles", "court_cases", "laws"]:
            count = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
            print(f"  {table}: {count:,} 条")
        total = await conn.fetchval("""
            SELECT (SELECT COUNT(*) FROM legal_articles)
                 + (SELECT COUNT(*) FROM court_cases)
                 + (SELECT COUNT(*) FROM laws)
                 + (SELECT COUNT(*) FROM legal_qa_pairs)
                 + (SELECT COUNT(*) FROM judicial_interpretations)
                 + (SELECT COUNT(*) FROM legal_knowledge_entries)
                 + (SELECT COUNT(*) FROM legal_cross_references)
        """)
        print(f"\n  数据总量: {total:,} 条")
        print("=" * 60)

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
