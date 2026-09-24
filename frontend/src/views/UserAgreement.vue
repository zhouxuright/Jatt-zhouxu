<template>
  <div class="legal-page">
    <div class="legal-container">
      <div class="legal-header">
        <router-link to="/login" class="back-link">
          <el-icon><ArrowLeft /></el-icon>
          <span>返回登录页</span>
        </router-link>
        <div class="logo-section">
          <el-icon :size="36" color="#1a365d"><ScaleToOriginal /></el-icon>
          <h1>法律智能辅助系统</h1>
        </div>
      </div>

      <el-card class="legal-card" shadow="never">
        <div class="document-title">
          <h2>用户协议</h2>
          <p class="update-date">最后更新日期：2026年8月29日</p>
          <p class="effective-date">生效日期：2026年8月29日</p>
        </div>

        <el-divider />

        <div class="document-body">
          <el-alert
            title="重要提示"
            type="warning"
            :closable="false"
            show-icon
            class="intro-alert"
          >
            <template #default>
              欢迎您使用法律智能辅助系统（以下简称"本系统"或"本平台"）。本用户协议（以下简称"本协议"）是您（以下简称"用户"或"您"）与本系统运营者（以下简称"我们"或"本平台"）之间关于使用本系统服务所订立的协议。<strong>请您在注册和使用本系统前，仔细阅读并充分理解本协议的全部内容，特别是免除或限制责任的条款、法律适用和争议解决条款。</strong>
            </template>
          </el-alert>

          <section class="legal-section">
            <h3>
              <el-icon><Service /></el-icon>
              一、服务描述
            </h3>

            <h4>1.1 服务内容</h4>
            <p>本系统基于人工智能技术，为用户提供以下法律智能辅助服务：</p>
            <ul>
              <li><strong>智能对话：</strong>通过AI助手回答法律咨询问题，提供法律知识解读</li>
              <li><strong>合同审查：</strong>对上传的合同文档进行条款分析和风险提示</li>
              <li><strong>文书生成：</strong>辅助生成各类法律文书草稿</li>
              <li><strong>法规检索：</strong>检索和提供法律法规条文信息</li>
              <li><strong>案例检索：</strong>检索和提供司法案例参考信息</li>
              <li>其他我们不时推出的新功能和服务</li>
            </ul>

            <h4>1.2 服务性质</h4>
            <el-alert
              type="error"
              :closable="false"
              show-icon
              class="disclaimer-alert"
            >
              <template #title>
                AI生成内容仅供参考声明
              </template>
              <template #default>
                <div class="disclaimer-content">
                  <p><strong>本系统提供的所有内容（包括但不限于AI回复、生成的文书、分析结果等）均仅供参考，不构成任何形式的法律建议或法律意见。</strong></p>
                  <p>本系统不能替代专业律师的服务。AI模型的回答可能存在不准确、不完整或不适用于特定情况的问题。对于重要法律事务，请务必咨询具有执业资格的专业律师。</p>
                  <p>您因依赖本系统提供的内容所做出的任何决策和行动，后果由您自行承担。</p>
                </div>
              </template>
            </el-alert>

            <h4>1.3 服务变更</h4>
            <p>我们有权根据需要不时修改、暂停或终止部分或全部服务，并将通过系统通知等方式提前告知用户。</p>
          </section>

          <el-divider />

          <section class="legal-section">
            <h3>
              <el-icon><UserFilled /></el-icon>
              二、用户账户
            </h3>

            <h4>2.1 账户注册</h4>
            <p>使用本系统服务前，您需要注册一个账户。注册时，您应当：</p>
            <ul>
              <li>提供真实、准确、完整的个人信息</li>
              <li>设置安全的密码并妥善保管</li>
              <li>不得以他人身份注册账户</li>
              <li>年满18周岁且具有完全民事行为能力</li>
            </ul>

            <h4>2.2 账户安全</h4>
            <p>您应对账户下的所有活动负责。如发现账户被未经授权的使用或存在安全漏洞，请立即通知我们。我们不对因您未能妥善保管密码而导致的任何损失承担责任。</p>

            <h4>2.3 账户注销</h4>
            <p>您可以随时申请注销账户。账户注销后，我们将按照《隐私政策》的规定处理您的个人信息。我们保留在必要时暂停或终止您账户的权利，包括但不限于以下情形：</p>
            <ul>
              <li>您违反本协议的任一条款</li>
              <li>您利用本系统从事违法活动</li>
              <li>长时间未使用账户</li>
              <li>法律法规规定的其他情形</li>
            </ul>
          </section>

          <el-divider />

          <section class="legal-section">
            <h3>
              <el-icon><Warning /></el-icon>
              三、用户义务与行为规范
            </h3>
            <p>在使用本系统时，您承诺遵守以下规定：</p>

            <h4>3.1 合法使用</h4>
            <ul>
              <li>遵守中华人民共和国法律法规</li>
              <li>不利用本系统从事任何违法、违规活动</li>
              <li>不侵犯他人的合法权益</li>
            </ul>

            <h4>3.2 内容规范</h4>
            <p>您在使用本系统时输入的内容不得包含以下信息：</p>
            <ul>
              <li>危害国家安全、泄露国家秘密的内容</li>
              <li>煽动民族仇恨、民族歧视的内容</li>
              <li>散布谣言、扰乱社会秩序的内容</li>
              <li>散布淫秽、色情、暴力、恐怖的内容</li>
              <li>侮辱或者诽谤他人、侵害他人合法权益的内容</li>
              <li>其他法律法规禁止的内容</li>
            </ul>

            <h4>3.3 禁止行为</h4>
            <p>您不得实施以下行为：</p>
            <ul>
              <li>对本系统进行反向工程、反向编译或反汇编</li>
              <li>利用技术手段恶意攻击本系统</li>
              <li>未经授权访问本系统的服务器或数据</li>
              <li>干扰或破坏本系统的正常运行</li>
              <li>将本系统服务用于商业目的（除非另有书面协议）</li>
              <li>将AI生成的内容作为正式法律意见提交给第三方</li>
            </ul>
          </section>

          <el-divider />

          <section class="legal-section">
            <h3>
              <el-icon><Aim /></el-icon>
              四、AI免责声明
            </h3>

            <el-card shadow="never" class="highlight-card">
              <h4>关于AI生成内容的特别提示</h4>
              <ol>
                <li><strong>非法律建议：</strong>本系统基于大语言模型技术生成回复和内容，这些内容仅供参考和学习使用，不构成法律建议、法律意见或任何其他专业建议。</li>
                <li><strong>可能存在错误：</strong>AI模型可能生成不准确、不完整或过时的信息。模型可能"幻觉"（hallucinate）不存在的法律条文、案例或解释。</li>
                <li><strong>不具有法律约束力：</strong>本系统生成的任何内容均不具有法律约束力，不能作为法律依据使用。</li>
                <li><strong>不替代专业律师：</strong>本系统不能替代具有执业资格的专业律师提供的法律服务。每个法律问题都有其特殊性，需要专业律师根据具体情况进行分析。</li>
                <li><strong>用户自行判断：</strong>用户应当自行判断AI生成内容的可靠性，并对基于该内容做出的任何决策和行动承担全部责任。</li>
                <li><strong>重要事项咨询专业人士：</strong>对于涉及重大权益的法律事务（如诉讼、合同签订、财产处置等），请务必咨询专业律师。</li>
              </ol>
            </el-card>
          </section>

          <el-divider />

          <section class="legal-section">
            <h3>
              <el-icon><CircleCheck /></el-icon>
              五、知识产权
            </h3>

            <h4>5.1 平台知识产权</h4>
            <p>本系统的所有内容，包括但不限于软件代码、界面设计、图标、文字、图片、音频、视频、算法模型等，其知识产权均归我们或相关权利人所有，受中华人民共和国著作权法、专利法、商标法及其他知识产权法律的保护。未经授权，不得复制、传播、修改或用于其他用途。</p>

            <h4>5.2 用户内容</h4>
            <p>您通过本系统输入的内容（如对话记录、上传文档等），其知识产权仍归您所有。但您授予我们以下许可：</p>
            <ul>
              <li>为提供服务所必需的使用权（如将您的输入发送至AI模型进行处理）</li>
              <li>为改进服务而进行的分析和研究（以匿名化和去标识化为前提）</li>
            </ul>

            <h4>5.3 AI生成内容</h4>
            <p>本系统AI生成的内容的知识产权归属，按照中华人民共和国相关法律法规的规定执行。在法律允许的范围内，我们对AI生成内容不承擔任何保证责任。</p>
          </section>

          <el-divider />

          <section class="legal-section">
            <h3>
              <el-icon><CircleClose /></el-icon>
              六、责任限制
            </h3>

            <el-alert
              type="error"
              :closable="false"
              show-icon
              class="disclaimer-alert"
            >
              <template #title>
                责任限制条款（请仔细阅读）
              </template>
              <template #default>
                <div class="disclaimer-content">
                  <ol>
                    <li><strong>按现状提供：</strong>本系统按"现状"和"可用性"基础提供服务，我们不对服务做任何明示或暗示的保证。</li>
                    <li><strong>不保证准确性：</strong>我们不保证AI生成的内容的准确性、完整性、可靠性或适用性。</li>
                    <li><strong>不保证连续性：</strong>我们不保证服务将不会中断或不会出现错误。因系统维护、升级或不可抗力导致的服务中断，我们不承担责任。</li>
                    <li><strong>使用风险自负：</strong>您使用本系统的风险由您自行承担。对于因使用或无法使用本系统而导致的任何直接、间接、附带、特殊或后果性损害，我们在法律允许的最大范围内不承担责任。</li>
                    <li><strong>第三方内容：</strong>本系统可能展示或链接至第三方的内容，我们不对第三方内容的准确性、合法性负责。</li>
                    <li><strong>赔偿限额：</strong>在法律允许的最大范围内，我们对您因使用本系统而遭受的全部赔偿不超过您在产生索赔事件前12个月内为本系统支付的费用（如有）。</li>
                  </ol>
                </div>
              </template>
            </el-alert>
          </section>

          <el-divider />

          <section class="legal-section">
            <h3>
              <el-icon><Lock /></el-icon>
              七、信息安全
            </h3>
            <p>我们重视您的信息安全，并采取合理的技术和管理措施保护您的个人信息。但请您理解，由于技术的限制以及可能存在的各种恶意手段，在互联网行业，不可能保证信息百分之百的安全。</p>
            <p>您需要了解您接入本系统的系统、设备和网络环境可能存在的安全风险，您有责任：</p>
            <ul>
              <li>妥善保管账户密码</li>
              <li>使用安全的网络环境</li>
              <li>不在公共设备上保存登录信息</li>
              <li>定期更换密码</li>
            </ul>
            <p>有关个人信息保护的详细内容，请参阅我们的《隐私政策》。</p>
          </section>

          <el-divider />

          <section class="legal-section">
            <h3>
              <el-icon><DocumentCopy /></el-icon>
              八、协议的变更与终止
            </h3>

            <h4>8.1 协议变更</h4>
            <p>我们有权根据需要不时修改本协议。修改后的协议将通过系统通知、弹窗提示等方式通知您。如果您不同意修改后的协议，您有权注销账户并停止使用本系统。如果您继续使用本系统，即视为您接受修改后的协议。</p>

            <h4>8.2 服务终止</h4>
            <p>在以下情形下，我们有权终止向您提供服务：</p>
            <ul>
              <li>您违反本协议的任何条款</li>
              <li>您利用本系统从事违法活动</li>
              <li>法律法规规定的其他情形</li>
              <li>我们认为有必要终止服务的其他合理情形</li>
            </ul>
            <p>终止服务后，您的账户将被冻结，我们将按照法律法规和《隐私政策》的要求处理您的个人信息。</p>
          </section>

          <el-divider />

          <section class="legal-section">
            <h3>
              <el-icon><ScaleToOriginal /></el-icon>
              九、争议解决
            </h3>
            <p>本协议的订立、执行、解释及争议解决均适用中华人民共和国法律（不包括港澳台地区法律）。</p>
            <p>因本协议产生的任何争议，双方应首先通过友好协商解决。协商不成的，任何一方均可向本系统运营者所在地有管辖权的人民法院提起诉讼。</p>
          </section>

          <el-divider />

          <section class="legal-section">
            <h3>
              <el-icon><List /></el-icon>
              十、其他条款
            </h3>
            <ul>
              <li><strong>完整性：</strong>本协议（连同《隐私政策》）构成您与我们之间关于使用本系统服务的完整协议。</li>
              <li><strong>可分割性：</strong>如本协议的任何条款被认定为无效或不可执行，该条款应以最接近原条款意图的方式予以修改，其余条款继续有效。</li>
              <li><strong>弃权：</strong>我们未能行使或延迟行使本协议下的任何权利，不构成对该权利的放弃。</li>
              <li><strong>通知：</strong>我们可能通过系统内通知、电子邮件或公告等方式向您发送通知，通知发出即视为送达。</li>
            </ul>
          </section>

          <el-divider />

          <section class="legal-section">
            <h3>
              <el-icon><Phone /></el-icon>
              十一、联系我们
            </h3>
            <p>如果您对本协议有任何疑问、意见或建议，请通过以下方式联系我们：</p>

            <el-card shadow="never" class="contact-card">
              <p><strong>法律智能辅助系统 法务团队</strong></p>
              <p>邮箱：legal@legal-ai-system.com</p>
              <p>我们将在收到您的信息后15个工作日内予以回复。</p>
            </el-card>
          </section>

          <div class="final-note">
            <el-alert
              type="info"
              :closable="false"
              show-icon
            >
              <template #title>
                注册或使用本系统即表示您已阅读、理解并同意接受本协议的全部内容。如您不同意本协议的任何内容，请勿注册或使用本系统。
              </template>
            </el-alert>
          </div>
        </div>
      </el-card>

      <div class="legal-footer">
        <p>&copy; 2026 法律智能辅助系统 版权所有</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import {
  ArrowLeft,
  Service,
  UserFilled,
  Warning,
  Aim,
  CircleCheck,
  CircleClose,
  Lock,
  DocumentCopy,
  ScaleToOriginal,
  List,
  Phone,
} from '@element-plus/icons-vue'
</script>

<style scoped>
.legal-page {
  min-height: 100vh;
  background: linear-gradient(135deg, #f0f4f8 0%, #e8eef5 100%);
  padding: 24px 16px;
}

.legal-container {
  max-width: 860px;
  margin: 0 auto;
}

.legal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 24px;
  padding: 0 4px;
}

.back-link {
  display: flex;
  align-items: center;
  gap: 4px;
  color: var(--primary-color);
  text-decoration: none;
  font-size: 14px;
  transition: color 0.2s;
}

.back-link:hover {
  color: var(--primary-light);
}

.logo-section {
  display: flex;
  align-items: center;
  gap: 10px;
}

.logo-section h1 {
  font-size: 18px;
  font-weight: 700;
  color: var(--primary-color);
  margin: 0;
}

.legal-card {
  border-radius: 12px;
  border: none;
}

.legal-card :deep(.el-card__body) {
  padding: 40px 48px;
}

.document-title {
  text-align: center;
  margin-bottom: 8px;
}

.document-title h2 {
  font-size: 28px;
  font-weight: 700;
  color: var(--primary-color);
  margin: 0 0 12px 0;
}

.update-date,
.effective-date {
  font-size: 13px;
  color: var(--text-secondary);
  margin: 4px 0;
}

.intro-alert {
  margin-bottom: 32px;
}

.intro-alert :deep(.el-alert__description) {
  line-height: 1.8;
  font-size: 14px;
}

.disclaimer-alert {
  margin: 16px 0;
}

.disclaimer-content p,
.disclaimer-content li {
  margin: 8px 0;
  line-height: 1.9;
}

.legal-section {
  margin-bottom: 8px;
}

.legal-section h3 {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 18px;
  font-weight: 600;
  color: var(--primary-color);
  margin: 0 0 16px 0;
}

.legal-section h4 {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 20px 0 8px 0;
}

.legal-section p {
  font-size: 14px;
  line-height: 1.9;
  color: var(--text-regular);
  margin: 8px 0;
}

.legal-section ul,
.legal-section ol {
  padding-left: 20px;
  margin: 8px 0;
}

.legal-section li {
  font-size: 14px;
  line-height: 2;
  color: var(--text-regular);
  margin: 4px 0;
}

.highlight-card {
  margin: 16px 0;
  background-color: #fff8e6;
  border-left: 4px solid #e6a23c;
}

.highlight-card :deep(.el-card__body) {
  padding: 24px;
}

.highlight-card h4 {
  color: #e6a23c;
  font-size: 16px;
  margin: 0 0 16px 0;
}

.highlight-card ol {
  padding-left: 20px;
}

.highlight-card li {
  line-height: 2;
  margin: 8px 0;
}

.contact-card {
  margin: 12px 0;
  background-color: #f8fafc;
}

.contact-card :deep(.el-card__body) {
  padding: 20px 24px;
}

.contact-card p {
  margin: 6px 0;
  font-size: 14px;
  line-height: 1.8;
}

.final-note {
  margin-top: 32px;
}

.legal-footer {
  text-align: center;
  padding: 24px 0;
  color: var(--text-secondary);
  font-size: 13px;
}

.legal-footer p {
  margin: 0;
}
</style>
