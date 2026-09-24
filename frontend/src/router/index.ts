import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    component: () => import('@/components/AppLayout.vue'),
    redirect: '/chat',
    children: [
      {
        path: 'chat',
        name: 'Chat',
        component: () => import('@/views/ChatView.vue'),
        meta: { title: '智能对话', requiresAuth: true },
      },
      {
        path: 'contract-review',
        name: 'ContractReview',
        component: () => import('@/views/ContractReview.vue'),
        meta: { title: '合同审查', requiresAuth: true },
      },
      {
        path: 'document-generate',
        name: 'DocumentGenerate',
        component: () => import('@/views/DocumentGenerate.vue'),
        meta: { title: '文书生成', requiresAuth: true },
      },
      {
        path: 'batch-upload',
        name: 'BatchUpload',
        component: () => import('@/views/BatchUpload.vue'),
        meta: { title: '批量处理', requiresAuth: true },
      },
      {
        path: 'law-search',
        name: 'LawSearch',
        component: () => import('@/views/LawSearch.vue'),
        meta: { title: '法规检索', requiresAuth: true },
      },
      {
        path: 'case-search',
        name: 'CaseSearch',
        component: () => import('@/views/CaseSearch.vue'),
        meta: { title: '案例检索', requiresAuth: true },
      },
      {
        path: 'deep-think',
        name: 'DeepThink',
        component: () => import('@/views/DeepThink.vue'),
        meta: { title: '深度推理', requiresAuth: true },
      },
      {
        path: 'skills',
        name: 'Skills',
        component: () => import('@/views/SkillsView.vue'),
        meta: { title: '技能包', requiresAuth: true },
      },
      {
        // 工具调用是调试界面：它直接展示 `/mcp/tools/execute` 的原始 JSON，
        // 且未配置 key 的工具（如 enterprise_lookup）会返回 demo_mode 占位数据。
        // 律师不应看到这些，因此移入 /admin 并加管理员校验。
        // 旧路径 /tools 保留重定向，避免已收藏的链接失效。
        path: 'admin/tools',
        name: 'AdminTools',
        component: () => import('@/views/ToolsView.vue'),
        meta: { title: '工具调用（管理）', requiresAuth: true, requiresAdmin: true },
      },
      {
        path: 'tools',
        redirect: '/admin/tools',
      },
      {
        path: 'litigation',
        name: 'Litigation',
        component: () => import('@/views/LitigationSupport.vue'),
        meta: { title: '诉讼支持', requiresAuth: true },
      },
      {
        path: 'compliance',
        name: 'Compliance',
        component: () => import('@/views/ComplianceRisk.vue'),
        meta: { title: '合规管理', requiresAuth: true },
      },
      {
        path: 'contract-lifecycle',
        name: 'ContractLifecycle',
        component: () => import('@/views/ContractLifecycle.vue'),
        meta: { title: '合同管理', requiresAuth: true },
      },
      {
        path: 'data-governance',
        name: 'DataGovernance',
        component: () => import('@/views/DataGovernance.vue'),
        meta: { title: '数据资产治理', requiresAuth: true },
      },
      {
        path: 'profile',
        name: 'Profile',
        component: () => import('@/views/Profile.vue'),
        meta: { title: '个人中心', requiresAuth: true },
      },
      {
        path: 'feedback',
        name: 'Feedback',
        redirect: '/profile',
        meta: { title: '反馈记录', requiresAuth: true },
      },
    ],
  },
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/Login.vue'),
    meta: { title: '登录', requiresAuth: false },
  },
  {
    path: '/register',
    name: 'Register',
    component: () => import('@/views/Register.vue'),
    meta: { title: '注册', requiresAuth: false },
  },
  {
    path: '/privacy',
    name: 'PrivacyPolicy',
    component: () => import('@/views/PrivacyPolicy.vue'),
    meta: { title: '隐私政策', requiresAuth: false },
  },
  {
    path: '/agreement',
    name: 'UserAgreement',
    component: () => import('@/views/UserAgreement.vue'),
    meta: { title: '用户协议', requiresAuth: false },
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'NotFound',
    redirect: '/chat',
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

// 路由守卫
router.beforeEach(async (to, _from, next) => {
  // 设置页面标题
  document.title = (to.meta.title as string) || '法律智能辅助系统'

  // 权限校验
  const requiresAuth = to.meta.requiresAuth as boolean | undefined
  const token = localStorage.getItem('token')

  if (requiresAuth && !token) {
    // 需要登录但没有 token → 跳转登录页
    next({ name: 'Login', query: { redirect: to.fullPath } })
    return
  }

  if (!requiresAuth && token && (to.name === 'Login' || to.name === 'Register')) {
    // 已登录但访问登录/注册页 → 跳转首页
    next({ name: 'Chat' })
    return
  }

  // 管理员页面：用户资料是懒加载的，直接刷新 /admin/* 时 store 里还没有 role,
  // 必须先取回资料再判断，否则管理员会被误挡在外面。
  if (to.meta.requiresAdmin) {
    const { useAuthStore } = await import('@/stores/auth')
    const auth = useAuthStore()
    if (!auth.user) {
      await auth.fetchUser()
    }
    if (!auth.isAdmin) {
      next({ name: 'Chat' })
      return
    }
  }

  next()
})

export default router
