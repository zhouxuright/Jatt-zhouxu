<template>
  <div class="register-page">
    <div class="register-card">
      <div class="register-header">
        <div class="logo-icon">
          <el-icon :size="40" color="#1a365d"><ScaleToOriginal /></el-icon>
        </div>
        <h1 class="register-title">创建新账号</h1>
        <p class="register-subtitle">注册法律智能辅助系统，享受专业法律服务</p>
      </div>

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        size="large"
        class="register-form"
        @submit.prevent="handleRegister"
      >
        <el-form-item prop="username">
          <el-input
            v-model="form.username"
            :prefix-icon="User"
            placeholder="请输入用户名"
            clearable
          />
        </el-form-item>

        <el-form-item prop="email">
          <el-input
            v-model="form.email"
            :prefix-icon="Message"
            placeholder="请输入邮箱地址"
            clearable
          />
        </el-form-item>

        <el-form-item prop="password">
          <el-input
            v-model="form.password"
            :prefix-icon="Lock"
            type="password"
            placeholder="请输入密码（至少6位）"
            show-password
          />
        </el-form-item>

        <el-form-item prop="confirmPassword">
          <el-input
            v-model="form.confirmPassword"
            :prefix-icon="Lock"
            type="password"
            placeholder="请再次输入密码"
            show-password
            @keyup.enter="handleRegister"
          />
        </el-form-item>

        <el-form-item class="terms-item">
          <el-checkbox v-model="agreedToTerms">
            我已阅读并同意
            <a
              href="/agreement"
              target="_blank"
              class="terms-link"
              @click.stop
            >《用户协议》</a>
            和
            <a
              href="/privacy"
              target="_blank"
              class="terms-link"
              @click.stop
            >《隐私政策》</a>
          </el-checkbox>
        </el-form-item>

        <el-form-item>
          <el-button
            type="primary"
            :loading="loading"
            class="register-btn"
            @click="handleRegister"
          >
            {{ loading ? '注册中...' : '注 册' }}
          </el-button>
        </el-form-item>
      </el-form>

      <div class="register-footer">
        <span>已有账号？</span>
        <router-link to="/login" class="login-link">立即登录</router-link>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { User, Lock, Message } from '@element-plus/icons-vue'
import type { FormInstance, FormRules } from 'element-plus'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const authStore = useAuthStore()

const formRef = ref<FormInstance>()
const loading = ref(false)
const agreedToTerms = ref(false)

const form = reactive({
  username: '',
  email: '',
  password: '',
  confirmPassword: '',
})

const validateConfirmPassword = (_rule: unknown, value: string, callback: (error?: Error) => void) => {
  if (value !== form.password) {
    callback(new Error('两次输入的密码不一致'))
  } else {
    callback()
  }
}

const rules: FormRules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 2, max: 30, message: '用户名长度在 2 到 30 个字符之间', trigger: 'blur' },
  ],
  email: [
    { required: true, message: '请输入邮箱地址', trigger: 'blur' },
    { type: 'email', message: '请输入有效的邮箱地址', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, max: 50, message: '密码长度在 6 到 50 个字符之间', trigger: 'blur' },
  ],
  confirmPassword: [
    { required: true, message: '请再次输入密码', trigger: 'blur' },
    { validator: validateConfirmPassword, trigger: 'blur' },
  ],
}

async function handleRegister() {
  if (!formRef.value) return

  await formRef.value.validate(async (valid) => {
    if (!valid) return

    if (!agreedToTerms.value) {
      ElMessage.warning('请先阅读并同意《用户协议》和《隐私政策》')
      return
    }

    loading.value = true
    try {
      await authStore.register({
        username: form.username,
        email: form.email,
        password: form.password,
        agreed_to_terms: true,
      })
      ElMessage.success('注册成功，请登录')
      router.push('/login')
    } catch {
      // 错误已在拦截器中处理
    } finally {
      loading.value = false
    }
  })
}
</script>

<style scoped>
.register-page {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #1a365d 0%, #2b6cb0 50%, #3182ce 100%);
}

.register-card {
  width: 420px;
  padding: 48px 40px;
  background-color: var(--bg-white);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
}

.register-header {
  text-align: center;
  margin-bottom: 36px;
}

.logo-icon {
  margin-bottom: 16px;
}

.register-title {
  font-size: 22px;
  font-weight: 700;
  color: var(--primary-color);
  margin: 0 0 8px 0;
}

.register-subtitle {
  font-size: 14px;
  color: var(--text-secondary);
  margin: 0;
}

.register-form {
  width: 100%;
}

.terms-item {
  margin-bottom: 20px;
}

.terms-item :deep(.el-form-item__content) {
  line-height: 1.6;
}

.terms-item :deep(.el-checkbox__label) {
  font-size: 13px;
  color: var(--text-regular);
  white-space: normal;
  line-height: 1.6;
}

.terms-link {
  color: var(--primary-light);
  font-weight: 500;
  text-decoration: none;
}

.terms-link:hover {
  color: var(--primary-lighter);
  text-decoration: underline;
}

.register-btn {
  width: 100%;
  height: 44px;
  font-size: 16px;
  letter-spacing: 4px;
}

.register-footer {
  text-align: center;
  margin-top: 24px;
  font-size: 14px;
  color: var(--text-secondary);
}

.login-link {
  color: var(--primary-light);
  font-weight: 500;
  margin-left: 4px;
}

.login-link:hover {
  color: var(--primary-lighter);
}
</style>
