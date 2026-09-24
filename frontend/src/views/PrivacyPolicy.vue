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
          <h2>隐私政策</h2>
          <p class="update-date">最后更新日期：2026年8月29日</p>
          <p class="effective-date">生效日期：2026年8月29日</p>
        </div>

        <el-divider />

        <div class="document-body">
          <el-alert
            title="重要提示"
            type="info"
            :closable="false"
            show-icon
            class="intro-alert"
          >
            <template #default>
              法律智能辅助系统（以下简称"本系统"或"我们"）非常重视您的个人隐私和信息安全。本隐私政策旨在向您说明我们如何收集、使用、存储和保护您的个人信息，以及您享有的相关权利。请您在使用本系统服务前，仔细阅读并充分理解本隐私政策的全部内容。如果您不同意本隐私政策的任何内容，请立即停止使用本系统服务。
            </template>
          </el-alert>

          <section class="legal-section">
            <h3>
              <el-icon><Document /></el-icon>
              一、信息收集
            </h3>
            <p>为向您提供法律智能辅助服务，我们可能收集以下类型的信息：</p>

            <h4>1.1 账户注册信息</h4>
            <ul>
              <li>用户名</li>
              <li>电子邮箱地址</li>
              <li>密码（以加密形式存储）</li>
              <li>账户创建时间</li>
            </ul>

            <h4>1.2 使用数据</h4>
            <ul>
              <li>您与本系统AI助机的对话记录（用于提供服务和改进系统）</li>
              <li>您上传或输入的文档内容（用于合同审查、文书生成等功能）</li>
              <li>您的操作日志（包括访问时间、功能使用记录等）</li>
              <li>设备信息（设备型号、操作系统版本、浏览器类型等）</li>
              <li>日志信息（IP地址、访问时间、请求记录等）</li>
            </ul>

            <h4>1.3 反馈信息</h4>
            <ul>
              <li>您对AI回复的反馈（赞同/反对）</li>
              <li>您主动提交的问题反馈和建议</li>
            </ul>
          </section>

          <el-divider />

          <section class="legal-section">
            <h3>
              <el-icon><Setting /></el-icon>
              二、信息使用
            </h3>
            <p>我们收集的信息将用于以下目的：</p>
            <ul>
              <li><strong>提供核心服务：</strong>处理您的法律咨询、合同审查、文书生成等请求，并通过AI模型生成相应的回复和建议。</li>
              <li><strong>账户管理：</strong>创建和维护您的用户账户，提供身份验证服务，保障账户安全。</li>
              <li><strong>服务改进：</strong>分析使用数据和反馈，优化AI模型性能，提升服务质量和用户体验。</li>
              <li><strong>安全保障：</strong>监测和防范欺诈、滥用等安全风险，维护系统的稳定运行。</li>
              <li><strong>合规义务：</strong>履行法律法规规定的义务，配合监管部门的合法要求。</li>
              <li><strong>通知沟通：</strong>向您发送服务变更、系统维护等重要通知。</li>
            </ul>
          </section>

          <el-divider />

          <section class="legal-section">
            <h3>
              <el-icon><FolderOpened /></el-icon>
              三、信息存储与保护
            </h3>

            <h4>3.1 存储地点</h4>
            <p>您的个人信息存储于中华人民共和国境内的服务器。我们不会将您的个人信息转移至中华人民共和国境外。</p>

            <h4>3.2 存储期限</h4>
            <p>我们仅在实现收集目的所必需的期限内保留您的个人信息。账户存续期间，我们将持续保留您的账户信息。当您注销账户后，我们将在合理期限内删除或匿名化处理您的个人信息，法律法规另有规定的除外。</p>

            <h4>3.3 安全措施</h4>
            <p>我们采取以下措施保护您的信息安全：</p>
            <ul>
              <li>采用行业标准的加密技术（如HTTPS/TLS）保护数据传输安全</li>
              <li>密码采用安全的哈希算法加密存储</li>
              <li>采用JWT令牌机制进行身份认证</li>
              <li>限制员工访问个人信息的权限</li>
              <li>定期进行安全评估和漏洞修复</li>
            </ul>

            <h4>3.4 安全事件应对</h4>
            <p>尽管我们会尽最大努力保护您的信息安全，但无法保证互联网环境的绝对安全。如发生个人信息泄露等安全事件，我们将按照法律法规的要求，及时向您告知事件基本情况、可能的影响、已采取的处理措施等，并向有关主管部门报告。</p>
          </section>

          <el-divider />

          <section class="legal-section">
            <h3>
              <el-icon><Connection /></el-icon>
              四、第三方服务
            </h3>
            <p>本系统可能涉及以下第三方服务：</p>

            <h4>4.1 AI大语言模型服务</h4>
            <p>本系统的核心功能依赖于AI大语言模型。您的对话内容和上传的文档可能会被发送至AI模型服务商进行处理。我们选择的AI服务商均符合中国法律法规要求，并承诺对处理的数据进行保密。</p>

            <h4>4.2 数据存储服务商</h4>
            <p>我们可能使用符合安全标准的云服务提供商存储您的数据。</p>

            <h4>4.3 第三方链接</h4>
            <p>本系统可能包含指向第三方网站的链接。我们对第三方网站的隐私保护实践不承担任何责任，建议您在访问时查阅其隐私政策。</p>

            <p><strong>特别说明：</strong>未经您的明确同意，我们不会向任何第三方共享、转让或出售您的个人信息，法律法规另有规定或司法机关依法要求的除外。</p>
          </section>

          <el-divider />

          <section class="legal-section">
            <h3>
              <el-icon><User /></el-icon>
              五、用户权利
            </h3>
            <p>根据《中华人民共和国个人信息保护法》，您享有以下权利：</p>

            <el-descriptions :column="1" border class="rights-table">
              <el-descriptions-item label="知情权">
                有权了解我们收集和使用您个人信息的规则
              </el-descriptions-item>
              <el-descriptions-item label="查阅权">
                有权查阅我们持有的您的个人信息
              </el-descriptions-item>
              <el-descriptions-item label="复制权">
                有权请求提供您的个人信息副本
              </el-descriptions-item>
              <el-descriptions-item label="更正权">
                当您发现个人信息有误时，有权请求更正
              </el-descriptions-item>
              <el-descriptions-item label="删除权">
                在法定情形下，有权请求删除您的个人信息
              </el-descriptions-item>
              <el-descriptions-item label="撤回同意权">
                有权撤回此前给予的同意（不影响撤回前基于同意已进行的信息处理的效力）
              </el-descriptions-item>
              <el-descriptions-item label="注销权">
                有权注销您的账户
              </el-descriptions-item>
              <el-descriptions-item label="投诉举报权">
                有权向履行个人信息保护职责的部门进行投诉举报
              </el-descriptions-item>
            </el-descriptions>

            <p class="note-text">如需行使上述权利，请通过本隐私政策底部提供的联系方式与我们联系。我们将在15个工作日内回复您的请求。</p>
          </section>

          <el-divider />

          <section class="legal-section">
            <h3>
              <el-icon><Delete /></el-icon>
              六、数据删除与账户注销
            </h3>

            <h4>6.1 账户注销</h4>
            <p>您可以随时申请注销您的账户。注销账户后，我们将：</p>
            <ul>
              <li>停止为您提供服务</li>
              <li>删除或匿名化处理您的个人信息</li>
              <li>法律法规要求保留的信息除外（如交易记录等）</li>
            </ul>

            <h4>6.2 数据删除</h4>
            <p>在以下情形中，您可以请求我们删除您的个人信息：</p>
            <ul>
              <li>处理目的已实现、无法实现或者为实现处理目的不再必要</li>
              <li>我们停止提供产品或者服务，或者保存期限已届满</li>
              <li>您撤回同意</li>
              <li>我们违反法律、行政法规或者违反约定处理个人信息</li>
            </ul>
          </section>

          <el-divider />

          <section class="legal-section">
            <h3>
              <el-icon><InfoFilled /></el-icon>
              七、未成年人保护
            </h3>
            <p>本系统仅面向年满18周岁的成年人。我们不会故意收集未成年人的个人信息。如果您发现我们无意中收集了未成年人的信息，请及时联系我们，我们将尽快删除相关数据。</p>
          </section>

          <el-divider />

          <section class="legal-section">
            <h3>
              <el-icon><RefreshRight /></el-icon>
              八、隐私政策的更新
            </h3>
            <p>我们可能会适时修订本隐私政策。当政策发生重大变更时，我们将通过系统通知、弹窗提示等显著方式告知您。如果您在隐私政策更新后继续使用本系统，即表示您同意接受更新后的隐私政策。</p>
          </section>

          <el-divider />

          <section class="legal-section">
            <h3>
              <el-icon><Phone /></el-icon>
              九、联系我们
            </h3>
            <p>如果您对本隐私政策有任何疑问、意见或建议，请通过以下方式联系我们：</p>

            <el-card shadow="never" class="contact-card">
              <p><strong>法律智能辅助系统 个人信息保护团队</strong></p>
              <p>邮箱：privacy@legal-ai-system.com</p>
              <p>我们将在收到您的信息后15个工作日内予以回复。</p>
            </el-card>
          </section>

          <el-divider />

          <section class="legal-section">
            <h3>
              <el-icon><ScaleToOriginal /></el-icon>
              十、法律适用与争议解决
            </h3>
            <p>本隐私政策的订立、执行、解释及争议解决均适用中华人民共和国法律（不包括港澳台地区法律）。因本隐私政策产生的争议，双方应友好协商解决；协商不成的，任何一方均可向本系统运营者所在地有管辖权的人民法院提起诉讼。</p>
          </section>

          <div class="final-note">
            <el-alert
              type="warning"
              :closable="false"
              show-icon
            >
              <template #title>
                本隐私政策依据《中华人民共和国个人信息保护法》《中华人民共和国网络安全法》《中华人民共和国数据安全法》等相关法律法规制定。
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
  Document,
  Setting,
  FolderOpened,
  Connection,
  User,
  Delete,
  InfoFilled,
  RefreshRight,
  Phone,
  ScaleToOriginal,
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

.legal-section ul {
  padding-left: 20px;
  margin: 8px 0;
}

.legal-section li {
  font-size: 14px;
  line-height: 2;
  color: var(--text-regular);
}

.note-text {
  font-size: 13px;
  color: var(--text-secondary);
  font-style: italic;
}

.rights-table {
  margin: 16px 0;
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
