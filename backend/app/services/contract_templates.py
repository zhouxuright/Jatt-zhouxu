"""
Contract Template Library -- Industry-standard contract templates.

Provides pre-built contract templates covering major business scenarios:
- 劳动合同 (Employment contracts)
- 买卖合同 (Sales contracts)
- 租赁合同 (Lease contracts)
- 借款合同 (Loan agreements)
- 服务合同 (Service agreements)
- 保密协议 (NDA / Confidentiality agreements)
- 技术合同 (Technology contracts)
- 合作协议 (Partnership/JV agreements)

Each template includes:
- Structured fields for fill-in
- Standard clauses with risk annotations
- Legal basis references
- Customizable terms
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# Template Data Model
# =============================================================================

@dataclass
class ContractTemplateField:
    """A fillable field in a contract template."""
    name: str
    label: str
    field_type: str  # "text" | "number" | "date" | "select" | "textarea"
    required: bool = True
    default: str = ""
    description: str = ""
    options: list[str] = field(default_factory=list)
    validation: str = ""  # regex pattern


@dataclass
class ContractTemplateClause:
    """A clause in a contract template."""
    id: str
    title: str
    content: str
    is_standard: bool = True
    risk_level: str = "low"  # low / medium / high
    risk_notes: str = ""
    legal_basis: str = ""
    customizable: bool = True


@dataclass
class ContractTemplate:
    """A complete contract template."""
    id: str
    name: str
    category: str
    description: str
    version: str = "1.0.0"
    legal_basis: str = ""
    fields: list[ContractTemplateField] = field(default_factory=list)
    clauses: list[ContractTemplateClause] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    usage_count: int = 0


# =============================================================================
# Built-in Contract Templates
# =============================================================================

def get_builtin_templates() -> list[dict[str, Any]]:
    """Return all built-in contract templates as serializable dicts."""
    return [
        _employment_contract(),
        _sales_contract(),
        _lease_contract(),
        _loan_agreement(),
        _service_agreement(),
        _nda_template(),
        _technology_contract(),
        _cooperation_agreement(),
        _software_license(),
        _freelance_contract(),
    ]


def _employment_contract() -> dict[str, Any]:
    """劳动合同模板"""
    return {
        "id": "employment_contract",
        "name": "劳动合同",
        "category": "劳动用工",
        "description": "标准劳动合同模板，适用于企业与员工签订劳动合同，涵盖工作内容、薪酬、福利、保密等核心条款",
        "version": "2.0.0",
        "legal_basis": "《中华人民共和国劳动合同法》",
        "tags": ["劳动", "用工", "雇佣", "薪酬"],
        "fields": [
            {"name": "employer_name", "label": "用人单位名称", "type": "text", "required": True},
            {"name": "employer_address", "label": "用人单位地址", "type": "text", "required": True},
            {"name": "legal_representative", "label": "法定代表人", "type": "text", "required": True},
            {"name": "employee_name", "label": "劳动者姓名", "type": "text", "required": True},
            {"name": "employee_id_number", "label": "身份证号码", "type": "text", "required": True},
            {"name": "employee_address", "label": "住址", "type": "text", "required": True},
            {"name": "position", "label": "工作岗位", "type": "text", "required": True},
            {"name": "work_location", "label": "工作地点", "type": "text", "required": True},
            {"name": "monthly_salary", "label": "月工资（元）", "type": "number", "required": True},
            {"name": "contract_start", "label": "合同起始日期", "type": "date", "required": True},
            {"name": "contract_end", "label": "合同终止日期", "type": "date", "required": True},
            {"name": "probation_months", "label": "试用期（月）", "type": "number", "required": False, "default": "0"},
            {"name": "probation_salary", "label": "试用期工资（元）", "type": "number", "required": False},
            {"name": "work_hours", "label": "工作时间制度", "type": "select", "required": True,
             "options": ["标准工时制", "综合计算工时制", "不定时工作制"]},
        ],
        "template_content": """劳动合同

甲方（用人单位）：{employer_name}
住所地：{employer_address}
法定代表人：{legal_representative}

乙方（劳动者）：{employee_name}
身份证号码：{employee_id_number}
住址：{employee_address}

根据《中华人民共和国劳动法》《中华人民共和国劳动合同法》及有关法律法规，甲乙双方在平等自愿、协商一致的基础上，签订本劳动合同。

第一条 合同期限
本合同为{contract_type}劳动合同。合同期限自{contract_start}起至{contract_end}止。其中试用期为{probation_months}个月，自{contract_start}起至{probation_end}止。

第二条 工作内容和工作地点
2.1 乙方同意根据甲方工作需要，担任{position}岗位工作。
2.2 乙方的工作地点为：{work_location}。
2.3 乙方应按照甲方的要求，按时完成规定的工作数量，达到规定的质量标准。

第三条 工作时间和休息休假
3.1 甲方安排乙方执行{work_hours}。
3.2 甲方应保证乙方每周至少休息一日，并依法享有法定节假日、年休假等假期权利。

第四条 劳动报酬
4.1 甲方每月{pay_day}日前以货币形式支付乙方工资。
4.2 乙方月工资为人民币{monthly_salary}元（税前）。
4.3 试用期工资为人民币{probation_salary}元（税前），不低于本合同约定工资的百分之八十或者本单位相同岗位最低档工资。
4.4 甲方根据生产经营状况和乙方的工作表现，可适当调整乙方的工资水平。

第五条 社会保险和福利待遇
5.1 甲方依法为乙方缴纳养老保险、医疗保险、失业保险、工伤保险和生育保险。
5.2 乙方患病或非因工负伤的医疗待遇按照国家和地方有关规定执行。
5.3 乙方因工负伤或患职业病的待遇按照国家和地方有关规定执行。

第六条 劳动保护、劳动条件和职业危害防护
6.1 甲方应为乙方提供符合国家规定的劳动安全卫生条件和必要的劳动防护用品。
6.2 甲方应对乙方进行劳动安全卫生教育和培训。

第七条 保密与竞业限制
7.1 乙方应对甲方的商业秘密和知识产权相关事项保密，保密期限为合同期内及离职后{confidentiality_years}年。
7.2 竞业限制条款由双方另行签订协议约定。

第八条 劳动合同的变更、解除和终止
8.1 经甲乙双方协商一致，可以变更本合同的相关内容。
8.2 甲乙双方解除或终止劳动合同，应依照《中华人民共和国劳动合同法》及有关规定执行。

第九条 违约责任
9.1 甲乙双方违反本合同约定的，应当承担相应的违约责任。
9.2 甲方违法解除或终止劳动合同的，应依照《劳动合同法》第八十七条规定支付赔偿金。

第十条 争议解决
因履行本合同发生的劳动争议，双方应协商解决；协商不成的，可以向劳动争议仲裁委员会申请仲裁。

第十一条 其他约定
{other_terms}

甲方（盖章）：                    乙方（签字）：
法定代表人（签字）：

签订日期：    年    月    日
""",
    }


def _sales_contract() -> dict[str, Any]:
    """买卖合同模板"""
    return {
        "id": "sales_contract",
        "name": "买卖合同",
        "category": "商事合同",
        "description": "标准买卖合同模板，适用于货物买卖交易，涵盖标的物、价款、交付、验收、违约等条款",
        "version": "1.0.0",
        "legal_basis": "《中华人民共和国民法典》第三编合同",
        "tags": ["买卖", "购销", "货物", "交易"],
        "fields": [
            {"name": "buyer_name", "label": "买方名称", "type": "text", "required": True},
            {"name": "seller_name", "label": "卖方名称", "type": "text", "required": True},
            {"name": "product_name", "label": "标的物名称", "type": "text", "required": True},
            {"name": "product_spec", "label": "规格型号", "type": "text", "required": True},
            {"name": "quantity", "label": "数量", "type": "text", "required": True},
            {"name": "unit_price", "label": "单价（元）", "type": "number", "required": True},
            {"name": "total_price", "label": "总价款（元）", "type": "number", "required": True},
            {"name": "delivery_date", "label": "交付日期", "type": "date", "required": True},
            {"name": "delivery_location", "label": "交付地点", "type": "text", "required": True},
            {"name": "payment_terms", "label": "付款方式", "type": "select", "required": True,
             "options": ["一次性付款", "分期付款", "预付款+尾款", "货到付款"]},
        ],
        "template_content": """买卖合同

合同编号：{contract_number}
签订日期：{sign_date}
签订地点：{sign_location}

买方（甲方）：{buyer_name}
卖方（乙方）：{seller_name}

根据《中华人民共和国民法典》的规定，甲乙双方在平等互利、协商一致的基础上，就甲方向乙方购买{product_name}事宜，达成如下协议：

第一条 标的物
1.1 品名：{product_name}
1.2 规格型号：{product_spec}
1.3 数量：{quantity}
1.4 质量标准：{quality_standard}

第二条 价款及支付
2.1 单价：人民币{unit_price}元
2.2 总价款：人民币{total_price}元（大写：{total_price_cn}）
2.3 付款方式：{payment_terms}
2.4 付款时间：{payment_schedule}

第三条 交付
3.1 交付时间：{delivery_date}
3.2 交付地点：{delivery_location}
3.3 运输方式：{transport_method}
3.4 运输费用：由{transport_cost_bearer}承担

第四条 验收
4.1 甲方应在收到标的物后{inspection_days}日内进行验收。
4.2 验收标准：按照本合同第1.4条约定的质量标准执行。
4.3 验收不合格的，甲方有权拒收或要求更换。

第五条 所有权转移和风险承担
5.1 标的物的所有权自交付时起转移，但甲方未支付全部价款前，乙方可保留所有权。
5.2 标的物毁损、灭失的风险，在标的物交付之前由出卖人承担，交付之后由买受人承担。

第六条 违约责任
6.1 乙方逾期交付标的物的，每逾期一日，应按总价款的{late_delivery_penalty}%向甲方支付违约金。
6.2 甲方逾期付款的，每逾期一日，应按未付金额的{late_payment_penalty}%向乙方支付违约金。

第七条 争议解决
本合同在履行过程中发生争议，由双方协商解决。协商不成的，{dispute_resolution}。

甲方（盖章）：                    乙方（盖章）：
授权代表：                        授权代表：
""",
    }


def _lease_contract() -> dict[str, Any]:
    """租赁合同模板"""
    return {
        "id": "lease_contract",
        "name": "房屋租赁合同",
        "category": "租赁合同",
        "description": "标准房屋租赁合同模板，适用于住宅或商业用房租赁",
        "version": "1.0.0",
        "legal_basis": "《中华人民共和国民法典》第七百零三条至第七百三十四条",
        "tags": ["租赁", "租房", "房屋", "租金"],
        "fields": [
            {"name": "landlord_name", "label": "出租方（甲方）", "type": "text", "required": True},
            {"name": "tenant_name", "label": "承租方（乙方）", "type": "text", "required": True},
            {"name": "property_address", "label": "房屋地址", "type": "text", "required": True},
            {"name": "property_area", "label": "房屋面积（㎡）", "type": "number", "required": True},
            {"name": "monthly_rent", "label": "月租金（元）", "type": "number", "required": True},
            {"name": "deposit", "label": "押金（元）", "type": "number", "required": True},
            {"name": "lease_start", "label": "租赁起始日期", "type": "date", "required": True},
            {"name": "lease_end", "label": "租赁终止日期", "type": "date", "required": True},
            {"name": "usage", "label": "用途", "type": "select", "required": True, "options": ["住宅", "商业办公", "仓储", "其他"]},
        ],
        "template_content": """房屋租赁合同

出租方（甲方）：{landlord_name}
承租方（乙方）：{tenant_name}

根据《中华人民共和国民法典》及有关规定，为明确甲乙双方的权利义务关系，经双方协商一致，订立本合同。

第一条 房屋基本情况
1.1 房屋坐落地址：{property_address}
1.2 房屋面积：{property_area}平方米
1.3 房屋用途：{usage}

第二条 租赁期限
2.1 租赁期自{lease_start}至{lease_end}，共计{lease_months}个月。
2.2 租赁期满，甲方有权收回房屋，乙方应如期交还。乙方如需续租，须在租期届满前{renewal_notice_days}日书面通知甲方。

第三条 租金及支付方式
3.1 月租金为人民币{monthly_rent}元。
3.2 支付方式：{payment_method}
3.3 乙方应于每月{rent_due_day}日前向甲方支付当月租金。

第四条 押金
4.1 乙方应于签订本合同时向甲方支付押金人民币{deposit}元。
4.2 租赁期满或合同解除后，甲方应在乙方交还房屋并结清各项费用后{deposit_return_days}日内无息退还押金。

第五条 房屋使用及维护
5.1 乙方应按约定用途使用房屋，不得擅自改变房屋结构。
5.2 房屋日常维修费用由{maintenance_bearer}承担。

第六条 合同解除与违约责任
6.1 任何一方提前解除合同，应提前{termination_notice_days}日书面通知对方。
6.2 乙方逾期支付租金的，每逾期一日，应按月租金的{late_payment_rate}%支付违约金。

第七条 争议解决
本合同发生争议，双方应协商解决。协商不成的，可以向房屋所在地人民法院提起诉讼。

甲方（签字/盖章）：                乙方（签字/盖章）：
日期：                            日期：
""",
    }


def _loan_agreement() -> dict[str, Any]:
    """借款合同模板"""
    return {
        "id": "loan_agreement",
        "name": "借款合同",
        "category": "金融合同",
        "description": "个人/企业借款合同模板，涵盖借款金额、利率、还款方式、担保等条款",
        "version": "1.0.0",
        "legal_basis": "《中华人民共和国民法典》第六百六十七条至第六百八十条",
        "tags": ["借款", "贷款", "利息", "还款"],
        "fields": [
            {"name": "lender_name", "label": "出借人", "type": "text", "required": True},
            {"name": "borrower_name", "label": "借款人", "type": "text", "required": True},
            {"name": "loan_amount", "label": "借款金额（元）", "type": "number", "required": True},
            {"name": "annual_rate", "label": "年利率（%）", "type": "number", "required": True},
            {"name": "loan_start", "label": "借款起始日期", "type": "date", "required": True},
            {"name": "loan_end", "label": "还款截止日期", "type": "date", "required": True},
            {"name": "repayment_method", "label": "还款方式", "type": "select", "required": True,
             "options": ["到期一次还本付息", "按月付息到期还本", "等额本息", "等额本金"]},
        ],
        "template_content": """借款合同

出借人（甲方）：{lender_name}
借款人（乙方）：{borrower_name}

根据《中华人民共和国民法典》及相关法律法规，甲乙双方经协商一致，订立本合同。

第一条 借款金额
甲方向乙方出借人民币{loan_amount}元（大写：{loan_amount_cn}）。

第二条 借款用途
乙方借款用于：{loan_purpose}。乙方不得将借款用于违法活动。

第三条 借款利率
年利率为{annual_rate}%，自实际放款之日起计算利息。

第四条 借款期限
借款期限自{loan_start}至{loan_end}，共计{loan_months}个月。

第五条 还款方式
乙方按照{repayment_method}方式偿还借款本息。

第六条 违约责任
6.1 乙方逾期还款的，应按逾期金额的{default_rate}%每日向甲方支付逾期利息。
6.2 乙方未按约定用途使用借款的，甲方有权提前收回借款。

第七条 争议解决
本合同争议由双方协商解决。协商不成的，任何一方均可向甲方所在地人民法院提起诉讼。

甲方（签字）：                    乙方（签字）：
日期：                            日期：
""",
    }


def _service_agreement() -> dict[str, Any]:
    """服务合同模板"""
    return {
        "id": "service_agreement",
        "name": "服务合同",
        "category": "商事合同",
        "description": "通用服务合同模板，适用于各类专业服务采购",
        "version": "1.0.0",
        "legal_basis": "《中华人民共和国民法典》",
        "tags": ["服务", "委托", "外包", "咨询"],
        "fields": [
            {"name": "client_name", "label": "委托方（甲方）", "type": "text", "required": True},
            {"name": "provider_name", "label": "服务方（乙方）", "type": "text", "required": True},
            {"name": "service_description", "label": "服务内容", "type": "textarea", "required": True},
            {"name": "service_fee", "label": "服务费用（元）", "type": "number", "required": True},
            {"name": "delivery_date", "label": "交付日期", "type": "date", "required": True},
            {"name": "acceptance_criteria", "label": "验收标准", "type": "textarea", "required": True},
        ],
        "template_content": """服务合同

委托方（甲方）：{client_name}
服务方（乙方）：{provider_name}

根据《中华人民共和国民法典》的规定，甲乙双方经友好协商，就乙方向甲方提供服务事宜达成如下协议：

第一条 服务内容
{service_description}

第二条 服务期限
服务期限自{start_date}至{delivery_date}。

第三条 服务费用及支付
3.1 服务费用总计：人民币{service_fee}元。
3.2 支付方式：{payment_terms}

第四条 验收标准
{acceptance_criteria}

第五条 知识产权
因履行本合同产生的知识产权，归{ip_owner}所有。

第六条 保密条款
双方应对在合同履行过程中知悉的对方商业秘密承担保密义务。

第七条 违约责任
任何一方违反本合同约定的，应向守约方支付合同总金额{breach_penalty_rate}%的违约金。

甲方（盖章）：                    乙方（盖章）：
日期：                            日期：
""",
    }


def _nda_template() -> dict[str, Any]:
    """保密协议模板"""
    return {
        "id": "nda",
        "name": "保密协议（NDA）",
        "category": "知识产权",
        "description": "保密协议模板，适用于商业合作中的商业秘密保护",
        "version": "1.0.0",
        "legal_basis": "《中华人民共和国反不正当竞争法》第九条",
        "tags": ["保密", "NDA", "商业秘密", "知识产权"],
        "fields": [
            {"name": "disclosing_party", "label": "披露方", "type": "text", "required": True},
            {"name": "receiving_party", "label": "接收方", "type": "text", "required": True},
            {"name": "confidential_info_scope", "label": "保密信息范围", "type": "textarea", "required": True},
            {"name": "confidentiality_period_years", "label": "保密期限（年）", "type": "number", "required": True, "default": "3"},
            {"name": "breach_penalty", "label": "违约金（元）", "type": "number", "required": True},
        ],
        "template_content": """保密协议

披露方：{disclosing_party}
接收方：{receiving_party}

鉴于双方正在进行业务合作，合作过程中可能涉及披露方的商业秘密和机密信息。为保护披露方的合法权益，双方经协商一致，达成如下保密协议：

第一条 保密信息的定义和范围
{confidential_info_scope}

第二条 保密义务
2.1 接收方应对保密信息严格保密，未经披露方书面同意，不得向任何第三方披露。
2.2 接收方仅可将保密信息用于双方合作之目的。
2.3 接收方应采取不低于保护自身同等重要保密信息的措施保护披露方的保密信息。

第三条 保密期限
保密期限为本协议签署之日起{confidentiality_period_years}年。

第四条 例外情形
下列信息不属于保密信息：
（一）接收方获得时已为公众所知的信息；
（二）非因接收方过错而成为公众所知的信息；
（三）接收方在获得前已合法持有的信息。

第五条 违约责任
接收方违反本协议约定的，应向披露方支付违约金人民币{breach_penalty}元，并赔偿由此给披露方造成的全部损失。

披露方（盖章）：                接收方（盖章）：
日期：                          日期：
""",
    }


def _technology_contract() -> dict[str, Any]:
    """技术合同模板"""
    return {
        "id": "technology_contract",
        "name": "技术开发合同",
        "category": "技术合同",
        "description": "技术开发（委托开发）合同模板",
        "version": "1.0.0",
        "legal_basis": "《中华人民共和国民法典》第三编第二十章技术合同",
        "tags": ["技术", "开发", "委托", "软件"],
        "fields": [
            {"name": "principal_name", "label": "委托方（甲方）", "type": "text", "required": True},
            {"name": "developer_name", "label": "开发方（乙方）", "type": "text", "required": True},
            {"name": "project_name", "label": "项目名称", "type": "text", "required": True},
            {"name": "technical_requirements", "label": "技术要求", "type": "textarea", "required": True},
            {"name": "development_fee", "label": "开发费用（元）", "type": "number", "required": True},
            {"name": "delivery_date", "label": "交付日期", "type": "date", "required": True},
        ],
        "template_content": """技术开发（委托开发）合同

委托方（甲方）：{principal_name}
开发方（乙方）：{developer_name}

根据《中华人民共和国民法典》的规定，合同双方就{project_name}项目的技术开发，经协商一致，签订本合同。

第一条 项目名称及内容
项目名称：{project_name}

第二条 技术要求和标准
{technical_requirements}

第三条 开发计划及进度
{development_schedule}

第四条 开发费用及支付
4.1 开发费用总计：人民币{development_fee}元。
4.2 支付方式：{payment_terms}

第五条 技术成果的归属
5.1 因履行本合同完成的技术成果及其知识产权归{ip_owner}所有。
5.2 开发方有权在不涉及委托方商业秘密的前提下，将开发中使用的通用技术用于其他项目。

第六条 验收
6.1 乙方完成开发后，应书面通知甲方进行验收。
6.2 甲方应在收到通知后{acceptance_days}日内组织验收。

第七条 保密
双方应对本合同内容及履行过程中知悉的对方商业秘密承担保密义务。

甲方（盖章）：                    乙方（盖章）：
日期：                            日期：
""",
    }


def _cooperation_agreement() -> dict[str, Any]:
    """合作协议模板"""
    return {
        "id": "cooperation_agreement",
        "name": "合作协议",
        "category": "商事合同",
        "description": "通用商业合作协议模板",
        "version": "1.0.0",
        "legal_basis": "《中华人民共和国民法典》",
        "tags": ["合作", "商务", "战略", "联盟"],
        "fields": [
            {"name": "party_a", "label": "甲方", "type": "text", "required": True},
            {"name": "party_b", "label": "乙方", "type": "text", "required": True},
            {"name": "cooperation_purpose", "label": "合作目的", "type": "textarea", "required": True},
            {"name": "party_a_obligations", "label": "甲方义务", "type": "textarea", "required": True},
            {"name": "party_b_obligations", "label": "乙方义务", "type": "textarea", "required": True},
            {"name": "profit_sharing", "label": "收益分配", "type": "textarea", "required": True},
            {"name": "cooperation_period", "label": "合作期限", "type": "text", "required": True},
        ],
        "template_content": """合作协议

甲方：{party_a}
乙方：{party_b}

甲乙双方本着平等互利、优势互补、共同发展的原则，经友好协商，就{cooperation_purpose}事宜达成如下合作协议：

第一条 合作目的
{cooperation_purpose}

第二条 合作内容
{cooperation_content}

第三条 双方权利与义务
3.1 甲方义务：{party_a_obligations}
3.2 乙方义务：{party_b_obligations}

第四条 收益分配
{profit_sharing}

第五条 合作期限
本协议合作期限为{cooperation_period}，自签署之日起生效。

第六条 违约责任
任何一方违反本协议约定的，应赔偿守约方因此遭受的全部损失。

第七条 争议解决
本协议争议由双方协商解决。协商不成的，提交{dispute_resolution}。

甲方（盖章）：                    乙方（盖章）：
日期：                            日期：
""",
    }


def _software_license() -> dict[str, Any]:
    """软件许可协议模板"""
    return {
        "id": "software_license",
        "name": "软件许可协议",
        "category": "技术合同",
        "description": "软件使用许可协议模板，适用于软件产品授权",
        "version": "1.0.0",
        "legal_basis": "《中华人民共和国著作权法》《计算机软件保护条例》",
        "tags": ["软件", "许可", "授权", "著作权"],
        "fields": [
            {"name": "licensor", "label": "许可方", "type": "text", "required": True},
            {"name": "licensee", "label": "被许可方", "type": "text", "required": True},
            {"name": "software_name", "label": "软件名称", "type": "text", "required": True},
            {"name": "license_type", "label": "许可类型", "type": "select", "required": True,
             "options": ["独占许可", "排他许可", "普通许可"]},
            {"name": "license_fee", "label": "许可费用（元）", "type": "number", "required": True},
            {"name": "license_scope", "label": "许可使用范围", "type": "textarea", "required": True},
        ],
        "template_content": """软件许可协议

许可方：{licensor}
被许可方：{licensee}

根据《中华人民共和国著作权法》《计算机软件保护条例》等法律法规，双方就{software_name}软件的使用许可事宜达成如下协议：

第一条 许可软件
软件名称：{software_name}
版本号：{software_version}

第二条 许可类型及范围
2.1 许可类型：{license_type}
2.2 许可使用范围：{license_scope}

第三条 许可费用
许可费用为人民币{license_fee}元。

第四条 知识产权
软件的全部知识产权归许可方所有。被许可方不得对软件进行反编译、反汇编或以其他方式试图获取软件源代码。

第五条 技术支持与维护
{support_terms}

许可方（盖章）：                被许可方（盖章）：
日期：                          日期：
""",
    }


def _freelance_contract() -> dict[str, Any]:
    """自由职业/外包服务合同模板"""
    return {
        "id": "freelance_contract",
        "name": "外包服务合同",
        "category": "商事合同",
        "description": "自由职业者/外包服务合同模板",
        "version": "1.0.0",
        "legal_basis": "《中华人民共和国民法典》",
        "tags": ["外包", "自由职业", "兼职", "委托"],
        "fields": [
            {"name": "client", "label": "委托方", "type": "text", "required": True},
            {"name": "contractor", "label": "服务方", "type": "text", "required": True},
            {"name": "project_description", "label": "项目描述", "type": "textarea", "required": True},
            {"name": "deliverables", "label": "交付物", "type": "textarea", "required": True},
            {"name": "fee", "label": "服务费用（元）", "type": "number", "required": True},
            {"name": "deadline", "label": "交付截止日期", "type": "date", "required": True},
        ],
        "template_content": """外包服务合同

委托方（甲方）：{client}
服务方（乙方）：{contractor}

双方经协商一致，就外包服务事宜签订本合同。

第一条 项目内容
{project_description}

第二条 交付物及验收标准
{deliverables}

第三条 交付时间
乙方应于{deadline}前完成全部工作并交付。

第四条 服务费用
4.1 费用总额：人民币{fee}元
4.2 支付方式：{payment_terms}

第五条 知识产权
因履行本合同产生的工作成果，知识产权归{ip_owner}所有。

第六条 保密
乙方应对甲方的商业秘密和项目信息保密。

甲方：                              乙方：
日期：                              日期：
""",
    }


# =============================================================================
# Template Engine
# =============================================================================

class ContractTemplateEngine:
    """Engine for managing and filling contract templates."""

    def __init__(self) -> None:
        self._templates: dict[str, dict[str, Any]] = {}
        self._load_builtin_templates()

    def _load_builtin_templates(self) -> None:
        """Load all built-in templates."""
        for template in get_builtin_templates():
            self._templates[template["id"]] = template
        logger.info("Loaded %d contract templates", len(self._templates))

    def list_templates(self, category: str | None = None) -> list[dict[str, Any]]:
        """List available templates, optionally filtered by category."""
        templates = self._templates.values()
        if category:
            templates = [t for t in templates if t.get("category") == category]
        return [
            {
                "id": t["id"],
                "name": t["name"],
                "category": t["category"],
                "description": t["description"],
                "legal_basis": t.get("legal_basis", ""),
                "tags": t.get("tags", []),
                "fields_count": len(t.get("fields", [])),
            }
            for t in templates
        ]

    def get_template(self, template_id: str) -> dict[str, Any] | None:
        """Get a template by ID."""
        return self._templates.get(template_id)

    def list_categories(self) -> list[str]:
        """List all template categories."""
        return sorted(set(t.get("category", "") for t in self._templates.values()))

    def fill_template(
        self, template_id: str, fields: dict[str, str]
    ) -> dict[str, Any]:
        """Fill a contract template with the provided field values.

        Args:
            template_id: Template ID.
            fields: Dict of field_name -> value.

        Returns:
            Dict with filled_content and any missing required fields.
        """
        template = self.get_template(template_id)
        if not template:
            return {"success": False, "error": f"Template '{template_id}' not found"}

        content = template.get("template_content", "")

        # Check required fields
        missing_fields = []
        for field_def in template.get("fields", []):
            fname = field_def["name"]
            if field_def.get("required", True) and not fields.get(fname):
                missing_fields.append({
                    "name": fname,
                    "label": field_def.get("label", fname),
                })

        # Replace placeholders
        filled_content = content
        for key, value in fields.items():
            placeholder = "{" + key + "}"
            filled_content = filled_content.replace(placeholder, str(value))

        # Mark unfilled placeholders
        import re
        unfilled = re.findall(r'\{(\w+)\}', filled_content)
        for field_name in unfilled:
            filled_content = filled_content.replace(
                "{" + field_name + "}",
                f"【待填写：{field_name}】"
            )

        return {
            "success": True,
            "template_id": template_id,
            "template_name": template.get("name", ""),
            "filled_content": filled_content,
            "missing_fields": missing_fields,
            "fields_provided": len(fields),
            "legal_basis": template.get("legal_basis", ""),
        }


# =============================================================================
# Singleton
# =============================================================================

_template_engine: ContractTemplateEngine | None = None


def get_contract_template_engine() -> ContractTemplateEngine:
    """Get the singleton ContractTemplateEngine instance."""
    global _template_engine
    if _template_engine is None:
        _template_engine = ContractTemplateEngine()
    return _template_engine
