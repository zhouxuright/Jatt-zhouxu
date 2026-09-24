"""Seed Data Loader for the Legal Intelligent Assistance System.

Populates the vector store and knowledge graph with foundational Chinese
legal knowledge including constitutional articles, criminal law provisions,
civil code articles, and common legal concepts.

Provides at least 20 real Chinese legal articles across all major legal
domains to bootstrap the RAG system.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from app.rag.vector_store import VectorStoreManager, embedding_provider
from app.rag.knowledge_graph import LegalKnowledgeGraph

logger = logging.getLogger(__name__)


# =========================================================================
# Constitutional Articles (中华人民共和国宪法)
# =========================================================================

CONSTITUTION_ARTICLES: List[Dict[str, Any]] = [
    {
        "article_id": "constitution_1",
        "title": "国体",
        "law_name": "中华人民共和国宪法",
        "article_number": "第一条",
        "content": (
            "中华人民共和国是工人阶级领导的、以工农联盟为基础的人民民主专政的社会主义国家。"
            "社会主义制度是中华人民共和国的根本制度。中国共产党领导是中国特色社会主义最本质的特征。"
            "禁止任何组织或者个人破坏社会主义制度。"
        ),
        "effective_date": "1982-12-04",
        "status": "active",
        "tags": ["宪法", "国体", "根本制度"],
    },
    {
        "article_id": "constitution_2",
        "title": "政体与权力归属",
        "law_name": "中华人民共和国宪法",
        "article_number": "第二条",
        "content": (
            "中华人民共和国的一切权力属于人民。"
            "人民行使国家权力的机关是全国人民代表大会和地方各级人民代表大会。"
            "人民依照法律规定，通过各种途径和形式，管理国家事务，管理经济和文化事业，管理社会事务。"
        ),
        "effective_date": "1982-12-04",
        "status": "active",
        "tags": ["宪法", "人民主权", "人民代表大会"],
    },
    {
        "article_id": "constitution_5",
        "title": "法治原则",
        "law_name": "中华人民共和国宪法",
        "article_number": "第五条",
        "content": (
            "中华人民共和国实行依法治国，建设社会主义法治国家。"
            "国家维护社会主义法制的统一和尊严。"
            "一切法律、行政法规和地方性法规都不得同宪法相抵触。"
            "一切国家机关和武装力量、各政党和各社会团体、各企业事业组织都必须遵守宪法和法律。"
            "一切违反宪法和法律的行为，必须予以追究。"
            "任何组织或者个人都不得有超越宪法和法律的特权。"
        ),
        "effective_date": "1982-12-04",
        "status": "active",
        "tags": ["宪法", "法治", "依法治国"],
    },
    {
        "article_id": "constitution_13",
        "title": "私有财产保护",
        "law_name": "中华人民共和国宪法",
        "article_number": "第十三条",
        "content": (
            "公民的合法的私有财产不受侵犯。"
            "国家依照法律规定保护公民的私有财产权和继承权。"
            "国家为了公共利益的需要，可以依照法律规定对公民的私有财产实行征收或者征用并给予补偿。"
        ),
        "effective_date": "1982-12-04",
        "status": "active",
        "tags": ["宪法", "财产权", "私有财产"],
    },
    {
        "article_id": "constitution_33",
        "title": "平等权与人权保障",
        "law_name": "中华人民共和国宪法",
        "article_number": "第三十三条",
        "content": (
            "凡具有中华人民共和国国籍的人都是中华人民共和国公民。"
            "中华人民共和国公民在法律面前一律平等。"
            "国家尊重和保障人权。"
            "任何公民享有宪法和法律规定的权利，同时必须履行宪法和法律规定的义务。"
        ),
        "effective_date": "1982-12-04",
        "status": "active",
        "tags": ["宪法", "平等权", "人权", "公民权利"],
    },
    {
        "article_id": "constitution_35",
        "title": "言论自由",
        "law_name": "中华人民共和国宪法",
        "article_number": "第三十五条",
        "content": (
            "中华人民共和国公民有言论、出版、集会、结社、游行、示威的自由。"
        ),
        "effective_date": "1982-12-04",
        "status": "active",
        "tags": ["宪法", "言论自由", "政治权利"],
    },
    {
        "article_id": "constitution_37",
        "title": "人身自由",
        "law_name": "中华人民共和国宪法",
        "article_number": "第三十七条",
        "content": (
            "中华人民共和国公民的人身自由不受侵犯。"
            "任何公民，非经人民检察院批准或者决定或者人民法院决定，并由公安机关执行，不受逮捕。"
            "禁止非法拘禁和以其他方法非法剥夺或者限制公民的人身自由，禁止非法搜查公民的身体。"
        ),
        "effective_date": "1982-12-04",
        "status": "active",
        "tags": ["宪法", "人身自由", "人身权利"],
    },
    {
        "article_id": "constitution_38",
        "title": "人格尊严",
        "law_name": "中华人民共和国宪法",
        "article_number": "第三十八条",
        "content": (
            "中华人民共和国公民的人格尊严不受侵犯。禁止用任何方法对公民进行侮辱、诽谤和诬告陷害。"
        ),
        "effective_date": "1982-12-04",
        "status": "active",
        "tags": ["宪法", "人格尊严", "名誉权"],
    },
]


# =========================================================================
# Criminal Law Articles (中华人民共和国刑法)
# =========================================================================

CRIMINAL_LAW_ARTICLES: List[Dict[str, Any]] = [
    {
        "article_id": "criminal_law_13",
        "title": "犯罪概念",
        "law_name": "中华人民共和国刑法",
        "article_number": "第十三条",
        "content": (
            "一切危害国家主权、领土完整和安全，分裂国家、颠覆人民民主专政的政权和推翻社会主义制度，"
            "破坏社会秩序和经济秩序，侵犯国有财产或者劳动群众集体所有的财产，侵犯公民私人所有的财产，"
            "侵犯公民的人身权利、民主权利和其他权利，以及其他危害社会的行为，依照法律应当受刑罚处罚的，"
            "都是犯罪，但是情节显著轻微危害不大的，不认为是犯罪。"
        ),
        "effective_date": "1997-10-01",
        "status": "active",
        "tags": ["刑法", "犯罪", "总则"],
    },
    {
        "article_id": "criminal_law_232",
        "title": "故意杀人罪",
        "law_name": "中华人民共和国刑法",
        "article_number": "第二百三十二条",
        "content": (
            "故意杀人的，处死刑、无期徒刑或者十年以上有期徒刑；"
            "情节较轻的，处三年以上十年以下有期徒刑。"
        ),
        "effective_date": "1997-10-01",
        "status": "active",
        "tags": ["刑法", "故意杀人", "人身权利"],
    },
    {
        "article_id": "criminal_law_234",
        "title": "故意伤害罪",
        "law_name": "中华人民共和国刑法",
        "article_number": "第二百三十四条",
        "content": (
            "故意伤害他人身体的，处三年以下有期徒刑、拘役或者管制。"
            "犯前款罪，致人重伤的，处三年以上十年以下有期徒刑；"
            "致人死亡或者以特别残忍手段致人重伤造成严重残疾的，处十年以上有期徒刑、"
            "无期徒刑或者死刑。本法另有规定的，依照规定。"
        ),
        "effective_date": "1997-10-01",
        "status": "active",
        "tags": ["刑法", "故意伤害", "人身权利"],
    },
    {
        "article_id": "criminal_law_264",
        "title": "盗窃罪",
        "law_name": "中华人民共和国刑法",
        "article_number": "第二百六十四条",
        "content": (
            "盗窃公私财物，数额较大的，或者多次盗窃、入户盗窃、携带凶器盗窃、扒窃的，"
            "处三年以下有期徒刑、拘役或者管制，并处或者单处罚金；"
            "数额巨大或者有其他严重情节的，处三年以上十年以下有期徒刑，并处罚金；"
            "数额特别巨大或者有其他特别严重情节的，处十年以上有期徒刑或者无期徒刑，"
            "并处罚金或者没收财产。"
        ),
        "effective_date": "1997-10-01",
        "status": "active",
        "tags": ["刑法", "盗窃", "财产犯罪"],
    },
    {
        "article_id": "criminal_law_266",
        "title": "诈骗罪",
        "law_name": "中华人民共和国刑法",
        "article_number": "第二百六十六条",
        "content": (
            "诈骗公私财物，数额较大的，处三年以下有期徒刑、拘役或者管制，并处或者单处罚金；"
            "数额巨大或者有其他严重情节的，处三年以上十年以下有期徒刑，并处罚金；"
            "数额特别巨大或者有其他特别严重情节的，处十年以上有期徒刑或者无期徒刑，"
            "并处罚金或者没收财产。本法另有规定的，依照规定。"
        ),
        "effective_date": "1997-10-01",
        "status": "active",
        "tags": ["刑法", "诈骗", "财产犯罪"],
    },
    {
        "article_id": "criminal_law_382",
        "title": "贪污罪",
        "law_name": "中华人民共和国刑法",
        "article_number": "第三百八十二条",
        "content": (
            "国家工作人员利用职务上的便利，侵吞、窃取、骗取或者以其他手段非法占有公共财物的，"
            "是贪污罪。受国家机关、国有公司、企业、事业单位、人民团体委托管理、经营国有财产的"
            "人员，利用职务上的便利，侵吞、窃取、骗取或者以其他手段非法占有国有财物的，以贪污论。"
            "与前两款所列人员勾结，伙同贪污的，以共犯论处。"
        ),
        "effective_date": "1997-10-01",
        "status": "active",
        "tags": ["刑法", "贪污", "职务犯罪"],
    },
    {
        "article_id": "criminal_law_385",
        "title": "受贿罪",
        "law_name": "中华人民共和国刑法",
        "article_number": "第三百八十五条",
        "content": (
            "国家工作人员利用职务上的便利，索取他人财物的，或者非法收受他人财物，"
            "为他人谋取利益的，是受贿罪。国家工作人员在经济往来中，违反国家规定，"
            "收受各种名义的回扣、手续费，归个人所有的，以受贿论处。"
        ),
        "effective_date": "1997-10-01",
        "status": "active",
        "tags": ["刑法", "受贿", "职务犯罪"],
    },
]


# =========================================================================
# Civil Code Articles (中华人民共和国民法典)
# =========================================================================

CIVIL_CODE_ARTICLES: List[Dict[str, Any]] = [
    {
        "article_id": "civil_code_2",
        "title": "民法调整对象",
        "law_name": "中华人民共和国民法典",
        "article_number": "第二条",
        "content": (
            "民法调整平等主体的自然人、法人和非法人组织之间的人身关系和财产关系。"
        ),
        "effective_date": "2021-01-01",
        "status": "active",
        "tags": ["民法典", "总则", "调整对象"],
    },
    {
        "article_id": "civil_code_3",
        "title": "民事权益受法律保护",
        "law_name": "中华人民共和国民法典",
        "article_number": "第三条",
        "content": (
            "民事主体的人身权利、财产权利以及其他合法权益受法律保护，任何组织或者个人不得侵犯。"
        ),
        "effective_date": "2021-01-01",
        "status": "active",
        "tags": ["民法典", "总则", "权益保护"],
    },
    {
        "article_id": "civil_code_464",
        "title": "合同定义",
        "law_name": "中华人民共和国民法典",
        "article_number": "第四百六十四条",
        "content": (
            "合同是民事主体之间设立、变更、终止民事法律关系的协议。"
            "婚姻、收养、监护等有关身份关系的协议，适用有关该身份关系的法律规定；"
            "没有规定的，可以根据其性质参照适用本编规定。"
        ),
        "effective_date": "2021-01-01",
        "status": "active",
        "tags": ["民法典", "合同", "合同编"],
    },
    {
        "article_id": "civil_code_509",
        "title": "合同履行原则",
        "law_name": "中华人民共和国民法典",
        "article_number": "第五百零九条",
        "content": (
            "当事人应当按照约定全面履行自己的义务。"
            "当事人应当遵循诚信原则，根据合同的性质、目的和交易习惯履行通知、协助、保密等义务。"
            "当事人在履行合同过程中，应当避免浪费资源、污染环境和破坏生态。"
        ),
        "effective_date": "2021-01-01",
        "status": "active",
        "tags": ["民法典", "合同", "合同履行", "诚信原则"],
    },
    {
        "article_id": "civil_code_577",
        "title": "违约责任",
        "law_name": "中华人民共和国民法典",
        "article_number": "第五百七十七条",
        "content": (
            "当事人一方不履行合同义务或者履行合同义务不符合约定的，"
            "应当承担继续履行、采取补救措施或者赔偿损失等违约责任。"
        ),
        "effective_date": "2021-01-01",
        "status": "active",
        "tags": ["民法典", "合同", "违约责任"],
    },
    {
        "article_id": "civil_code_1046",
        "title": "结婚自愿",
        "law_name": "中华人民共和国民法典",
        "article_number": "第一千零四十六条",
        "content": (
            "结婚应当男女双方完全自愿，禁止任何一方对另一方加以强迫，"
            "禁止任何组织或者个人加以干涉。"
        ),
        "effective_date": "2021-01-01",
        "status": "active",
        "tags": ["民法典", "婚姻家庭", "结婚"],
    },
    {
        "article_id": "civil_code_1079",
        "title": "诉讼离婚",
        "law_name": "中华人民共和国民法典",
        "article_number": "第一千零七十九条",
        "content": (
            "夫妻一方要求离婚的，可以由有关组织进行调解或者直接向人民法院提起离婚诉讼。"
            "人民法院审理离婚案件，应当进行调解；如果感情确已破裂，调解无效的，应当准予离婚。"
            "有下列情形之一，调解无效的，应当准予离婚：（一）重婚或者与他人同居；"
            "（二）实施家庭暴力或者虐待、遗弃家庭成员；"
            "（三）有赌博、吸毒等恶习屡教不改；"
            "（四）因感情不和分居满二年；"
            "（五）其他导致夫妻感情破裂的情形。"
        ),
        "effective_date": "2021-01-01",
        "status": "active",
        "tags": ["民法典", "婚姻家庭", "离婚"],
    },
    {
        "article_id": "civil_code_1165",
        "title": "过错责任原则",
        "law_name": "中华人民共和国民法典",
        "article_number": "第一千一百六十五条",
        "content": (
            "行为人因过错侵害他人民事权益造成损害的，应当承担侵权责任。"
            "依照法律规定推定行为人有过错，其不能证明自己没有过错的，应当承担侵权责任。"
        ),
        "effective_date": "2021-01-01",
        "status": "active",
        "tags": ["民法典", "侵权责任", "过错责任"],
    },
    {
        "article_id": "civil_code_1167",
        "title": "侵权责任承担方式",
        "law_name": "中华人民共和国民法典",
        "article_number": "第一千一百六十七条",
        "content": (
            "侵权行为危及他人人身、财产安全的，被侵权人有权请求侵权人承担停止侵害、"
            "排除妨碍、消除危险等侵权责任。"
        ),
        "effective_date": "2021-01-01",
        "status": "active",
        "tags": ["民法典", "侵权责任", "责任承担"],
    },
    {
        "article_id": "civil_code_1254",
        "title": "高空抛物责任",
        "law_name": "中华人民共和国民法典",
        "article_number": "第一千二百五十四条",
        "content": (
            "禁止从建筑物中抛掷物品。从建筑物中抛掷物品或者从建筑物上坠落的物品造成他人损害的，"
            "由侵权人依法承担侵权责任；经调查难以确定具体侵权人的，除能够证明自己不是侵权人的外，"
            "由可能加害的建筑物使用人给予补偿。可能加害的建筑物使用人补偿后，有权向侵权人追偿。"
            "物业服务企业等建筑物管理人应当采取必要的安全保障措施防止前款规定情形的发生；"
            "未采取必要的安全保障措施的，应当依法承担未履行安全保障义务的侵权责任。"
        ),
        "effective_date": "2021-01-01",
        "status": "active",
        "tags": ["民法典", "侵权责任", "高空抛物"],
    },
]


# =========================================================================
# Common Legal Concepts
# =========================================================================

LEGAL_CONCEPTS: List[Dict[str, Any]] = [
    {
        "concept_id": "concept_rule_of_law",
        "name": "依法治国",
        "definition": (
            "依法治国是中国共产党领导人民治理国家的基本方略，要求国家各项工作都依法进行，"
            "逐步实现社会主义民主的制度化、法律化，使这种制度和法律不因领导人的改变而改变，"
            "不因领导人看法和注意力的改变而改变。"
        ),
        "category": "宪法原则",
        "related_articles": ["constitution_5"],
    },
    {
        "concept_id": "concept_presumption_of_innocence",
        "name": "无罪推定原则",
        "definition": (
            "未经人民法院依法判决，对任何人都不得确定有罪。"
            "该原则要求控方承担证明被告人有罪的举证责任，"
            "被告人不负有证明自己无罪的义务。"
        ),
        "category": "刑事诉讼原则",
        "related_articles": ["criminal_law_13"],
    },
    {
        "concept_id": "concept_good_faith",
        "name": "诚实信用原则",
        "definition": (
            "民事主体从事民事活动，应当遵循诚信原则，秉持诚实，恪守承诺。"
            "诚实信用原则是民法的基本原则之一，被称为'帝王条款'，"
            "贯穿于民事活动的全过程，是民事主体行使权利、履行义务的基本准则。"
        ),
        "category": "民法原则",
        "related_articles": ["civil_code_509"],
    },
    {
        "concept_id": "concept_tort_liability",
        "name": "侵权责任",
        "definition": (
            "侵权责任是指民事主体因实施侵权行为而应承担的民事法律后果。"
            "侵权责任的构成要件一般包括：行为、损害事实、因果关系和过错。"
            "侵权责任的承担方式包括停止侵害、排除妨碍、消除危险、返还财产、"
            "恢复原状、赔偿损失、赔礼道歉、消除影响、恢复名誉等。"
        ),
        "category": "民法",
        "related_articles": ["civil_code_1165", "civil_code_1167"],
    },
    {
        "concept_id": "concept_contract_breach",
        "name": "合同违约",
        "definition": (
            "合同违约是指合同当事人一方不履行合同义务或者履行合同义务不符合约定的行为。"
            "违约责任的承担方式包括：继续履行、采取补救措施、赔偿损失、支付违约金等。"
            "赔偿损失的范围应当相当于因违约所造成的损失，包括合同履行后可以获得的利益，"
            "但不得超过违约方订立合同时预见到或者应当预见到的因违约可能造成的损失。"
        ),
        "category": "合同法",
        "related_articles": ["civil_code_577"],
    },
    {
        "concept_id": "concept_criminal_intent",
        "name": "犯罪故意",
        "definition": (
            "明知自己的行为会发生危害社会的结果，并且希望或者放任这种结果发生，"
            "因而构成犯罪的，是故意犯罪。故意犯罪，应当负刑事责任。"
            "犯罪故意分为直接故意和间接故意：直接故意是指行为人明知自己的行为"
            "必然或者可能发生危害社会的结果，并且希望这种结果发生的心理态度；"
            "间接故意是指行为人明知自己的行为可能发生危害社会的结果，"
            "并且放任这种结果发生的心理态度。"
        ),
        "category": "刑法",
        "related_articles": ["criminal_law_232", "criminal_law_234"],
    },
    {
        "concept_id": "concept_embezzlement",
        "name": "贪污贿赂犯罪",
        "definition": (
            "贪污贿赂犯罪是指国家工作人员利用职务上的便利，侵吞、窃取、骗取或者"
            "以其他手段非法占有公共财物，或者非法收受他人财物为他人谋取利益的行为，"
            "以及为谋取不正当利益给予国家工作人员财物的行为。"
            "主要包括贪污罪、受贿罪、行贿罪、挪用公款罪、巨额财产来源不明罪等。"
        ),
        "category": "刑法",
        "related_articles": ["criminal_law_382", "criminal_law_385"],
    },
    {
        "concept_id": "concept_marriage_property",
        "name": "夫妻共同财产",
        "definition": (
            "夫妻在婚姻关系存续期间所得的下列财产，为夫妻的共同财产，归夫妻共同所有："
            "（一）工资、奖金、劳务报酬；（二）生产、经营、投资的收益；"
            "（三）知识产权的收益；（四）继承或者受赠的财产，但是遗嘱或者赠与合同中"
            "确定只归一方的财产除外；（五）其他应当归共同所有的财产。"
            "夫妻对共同财产，有平等的处理权。"
        ),
        "category": "婚姻家庭法",
        "related_articles": ["civil_code_1046", "civil_code_1079"],
    },
]


# =========================================================================
# SeedDataLoader
# =========================================================================

class SeedDataLoader:
    """Loads initial legal knowledge into the vector store and knowledge graph.

    Usage::

        loader = SeedDataLoader()
        loader.load_all()
    """

    def __init__(
        self,
        vector_store: Optional[VectorStoreManager] = None,
        knowledge_graph: Optional[LegalKnowledgeGraph] = None,
    ) -> None:
        from app.rag.vector_store import VectorStoreManager
        from app.rag.knowledge_graph import LegalKnowledgeGraph

        self._vs = vector_store or VectorStoreManager()
        self._kg = knowledge_graph or LegalKnowledgeGraph()

    # ------------------------------------------------------------------
    # Individual loaders
    # ------------------------------------------------------------------

    def load_constitution(self) -> int:
        """Load constitutional articles into vector store and knowledge graph."""
        logger.info("Loading %d constitutional articles...", len(CONSTITUTION_ARTICLES))
        self._load_articles(CONSTITUTION_ARTICLES, "legal_articles")
        self._kg.load_articles_batch(CONSTITUTION_ARTICLES)
        return len(CONSTITUTION_ARTICLES)

    def load_criminal_law(self) -> int:
        """Load criminal law articles into vector store and knowledge graph."""
        logger.info("Loading %d criminal law articles...", len(CRIMINAL_LAW_ARTICLES))
        self._load_articles(CRIMINAL_LAW_ARTICLES, "legal_articles")
        self._kg.load_articles_batch(CRIMINAL_LAW_ARTICLES)
        return len(CRIMINAL_LAW_ARTICLES)

    def load_civil_code(self) -> int:
        """Load civil code articles into vector store and knowledge graph."""
        logger.info("Loading %d civil code articles...", len(CIVIL_CODE_ARTICLES))
        self._load_articles(CIVIL_CODE_ARTICLES, "legal_articles")
        self._kg.load_articles_batch(CIVIL_CODE_ARTICLES)
        return len(CIVIL_CODE_ARTICLES)

    def load_common_legal_concepts(self) -> int:
        """Load common legal concepts into vector store and knowledge graph."""
        logger.info("Loading %d legal concepts...", len(LEGAL_CONCEPTS))
        self._load_concepts(LEGAL_CONCEPTS, "legal_knowledge")
        for concept in LEGAL_CONCEPTS:
            try:
                self._kg.create_concept_node(concept)
            except Exception as exc:
                logger.error("Failed to load concept %s: %s", concept.get("name"), exc)
        return len(LEGAL_CONCEPTS)

    # ------------------------------------------------------------------
    # Load all
    # ------------------------------------------------------------------

    def load_all(self) -> Dict[str, int]:
        """Load all seed data and return counts per category."""
        self._vs.init_collections()

        counts = {
            "constitution": self.load_constitution(),
            "criminal_law": self.load_criminal_law(),
            "civil_code": self.load_civil_code(),
            "legal_concepts": self.load_common_legal_concepts(),
        }

        # Build cross-article relationships in the knowledge graph
        self._build_relationships()

        total = sum(counts.values())
        logger.info(
            "Seed data loaded: %s (total: %d articles + concepts)",
            counts,
            total,
        )
        return counts

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_articles(
        self,
        articles: List[Dict[str, Any]],
        collection_name: str,
    ) -> None:
        """Index articles into the vector store."""
        texts = [art["content"] for art in articles]
        metadatas = [
            {
                "article_id": art["article_id"],
                "title": art["title"],
                "law_name": art["law_name"],
                "article_number": art["article_number"],
                "tags": ",".join(art.get("tags", [])),
            }
            for art in articles
        ]
        ids = [art["article_id"] for art in articles]

        try:
            embeddings = embedding_provider.embed_documents(texts)
            self._vs.add_documents(
                collection_name,
                texts,
                embeddings,
                metadatas=metadatas,
                ids=ids,
            )
            logger.info(
                "Indexed %d articles into collection '%s'",
                len(articles),
                collection_name,
            )
        except Exception as exc:
            logger.error("Failed to index articles: %s", exc)

    def _load_concepts(
        self,
        concepts: List[Dict[str, Any]],
        collection_name: str,
    ) -> None:
        """Index concepts into the vector store."""
        texts = [
            f"{c['name']}：{c['definition']}" for c in concepts
        ]
        metadatas = [
            {
                "concept_id": c["concept_id"],
                "name": c["name"],
                "category": c.get("category", ""),
                "related_articles": ",".join(c.get("related_articles", [])),
            }
            for c in concepts
        ]
        ids = [c["concept_id"] for c in concepts]

        try:
            embeddings = embedding_provider.embed_documents(texts)
            self._vs.add_documents(
                collection_name,
                texts,
                embeddings,
                metadatas=metadatas,
                ids=ids,
            )
            logger.info(
                "Indexed %d concepts into collection '%s'",
                len(concepts),
                collection_name,
            )
        except Exception as exc:
            logger.error("Failed to index concepts: %s", exc)

    def _build_relationships(self) -> None:
        """Build cross-article relationships in the knowledge graph."""
        # Criminal law articles are related to each other
        for i in range(len(CRIMINAL_LAW_ARTICLES)):
            for j in range(i + 1, len(CRIMINAL_LAW_ARTICLES)):
                art_a = CRIMINAL_LAW_ARTICLES[i]
                art_b = CRIMINAL_LAW_ARTICLES[j]
                # Connect articles with shared tags
                if set(art_a.get("tags", [])) & set(art_b.get("tags", [])):
                    self._kg.add_relationship(
                        art_a["article_id"],
                        art_b["article_id"],
                        "RELATED_TO",
                    )

        # Civil code articles are related to each other
        for i in range(len(CIVIL_CODE_ARTICLES)):
            for j in range(i + 1, len(CIVIL_CODE_ARTICLES)):
                art_a = CIVIL_CODE_ARTICLES[i]
                art_b = CIVIL_CODE_ARTICLES[j]
                if set(art_a.get("tags", [])) & set(art_b.get("tags", [])):
                    self._kg.add_relationship(
                        art_a["article_id"],
                        art_b["article_id"],
                        "RELATED_TO",
                    )

        # Connect concepts to their related articles
        for concept in LEGAL_CONCEPTS:
            for related_id in concept.get("related_articles", []):
                try:
                    self._kg.add_relationship(
                        concept["concept_id"],
                        related_id,
                        "RELATED_TO",
                    )
                except Exception as exc:
                    logger.debug(
                        "Could not relate concept %s to %s: %s",
                        concept["concept_id"],
                        related_id,
                        exc,
                    )

        logger.info("Knowledge graph relationships built")


# =========================================================================
# Convenience function
# =========================================================================

def load_seed_data() -> Dict[str, int]:
    """Load all seed data.  Convenience function for one-liner seeding."""
    loader = SeedDataLoader()
    return loader.load_all()