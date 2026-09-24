<template>
  <div class="app-layout">
    <!-- 侧边栏 -->
    <el-aside :width="isCollapsed ? '64px' : '240px'" class="app-sidebar">
      <div class="sidebar-header">
        <div class="logo-area" @click="router.push('/chat')">
          <el-icon :size="24" color="#fff"><ScaleToOriginal /></el-icon>
          <span v-show="!isCollapsed" class="logo-text">法律智能辅助</span>
        </div>
      </div>

      <el-menu
        :default-active="activeMenu"
        :collapse="isCollapsed"
        :collapse-transition="false"
        background-color="#1a365d"
        text-color="#a0c4e8"
        active-text-color="#ffffff"
        router
        class="sidebar-menu"
      >
        <el-menu-item index="/chat">
          <el-icon><ChatDotRound /></el-icon>
          <template #title>智能对话</template>
        </el-menu-item>
        <el-menu-item index="/contract-review">
          <el-icon><Document /></el-icon>
          <template #title>合同审查</template>
        </el-menu-item>
        <el-menu-item index="/document-generate">
          <el-icon><EditPen /></el-icon>
          <template #title>文书生成</template>
        </el-menu-item>
        <el-menu-item index="/batch-upload">
          <el-icon><UploadFilled /></el-icon>
          <template #title>批量处理</template>
        </el-menu-item>
        <el-menu-item index="/law-search">
          <el-icon><Search /></el-icon>
          <template #title>法规检索</template>
        </el-menu-item>
        <el-menu-item index="/case-search">
          <el-icon><Files /></el-icon>
          <template #title>案例检索</template>
        </el-menu-item>
        <el-menu-item index="/deep-think">
          <el-icon><Cpu /></el-icon>
          <template #title>深度推理</template>
        </el-menu-item>
        <el-menu-item index="/skills">
          <el-icon><SetUp /></el-icon>
          <template #title>技能包</template>
        </el-menu-item>
        <!-- 工具调用已移入管理区（详见 router/index.ts 注释）：它是调试界面，
             会展示原始 JSON 与 demo_mode 占位数据，不适合出现在律师的主菜单。 -->
        <el-menu-item index="/litigation">
          <el-icon><Sort /></el-icon>
          <template #title>诉讼支持</template>
        </el-menu-item>
        <el-menu-item index="/compliance">
          <el-icon><Lock /></el-icon>
          <template #title>合规管理</template>
        </el-menu-item>
        <el-menu-item index="/contract-lifecycle">
          <el-icon><Tickets /></el-icon>
          <template #title>合同管理</template>
        </el-menu-item>
        <el-menu-item index="/data-governance">
          <el-icon><DataLine /></el-icon>
          <template #title>数据资产治理</template>
        </el-menu-item>
      </el-menu>

      <div class="sidebar-footer">
        <el-menu
          :default-active="activeMenu"
          :collapse="isCollapsed"
          :collapse-transition="false"
          background-color="#1a365d"
          text-color="#a0c4e8"
          active-text-color="#ffffff"
          router
        >
          <el-menu-item v-if="isAdmin" index="/admin/tools">
            <el-icon><Connection /></el-icon>
            <template #title>工具调用（管理）</template>
          </el-menu-item>
          <el-menu-item index="/profile">
            <el-icon><User /></el-icon>
            <template #title>个人中心</template>
          </el-menu-item>
        </el-menu>
        <div class="collapse-btn" @click="isCollapsed = !isCollapsed">
          <el-icon :size="18">
            <Fold v-if="!isCollapsed" />
            <Expand v-else />
          </el-icon>
        </div>
      </div>
    </el-aside>

    <!-- 主内容区 -->
    <div class="app-main">
      <!-- 顶部栏 -->
      <header class="app-header">
        <div class="header-left">
          <h2 class="page-title">{{ pageTitle }}</h2>
        </div>
        <div class="header-right">
          <el-dropdown trigger="click" @command="handleUserCommand">
            <span class="user-info">
              <el-avatar :size="32" :icon="UserFilled" />
              <span class="username">{{ authStore.user?.username || '用户' }}</span>
              <el-icon><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="profile">
                  <el-icon><User /></el-icon>个人中心
                </el-dropdown-item>
                <el-dropdown-item command="logout" divided>
                  <el-icon><SwitchButton /></el-icon>退出登录
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </header>

      <!-- 内容区域 -->
      <main class="app-content">
        <router-view />
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { UserFilled } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const isAdmin = computed(() => authStore.isAdmin)

const isCollapsed = ref(localStorage.getItem('sidebar-collapsed') === 'true')

watch(isCollapsed, (val) => localStorage.setItem('sidebar-collapsed', String(val)))

const activeMenu = computed(() => {
  const path = route.path
  if (path.startsWith('/chat')) return '/chat'
  if (path.startsWith('/contract-review')) return '/contract-review'
  if (path.startsWith('/document-generate')) return '/document-generate'
  if (path.startsWith('/batch-upload')) return '/batch-upload'
  if (path.startsWith('/law-search')) return '/law-search'
  if (path.startsWith('/case-search')) return '/case-search'
  if (path.startsWith('/deep-think')) return '/deep-think'
  if (path.startsWith('/skills')) return '/skills'
  // 工具调用已移入 /admin/tools（见 router/index.ts）：这里必须用完整路径匹配，
  // 因为 '/admin/tools'.startsWith('/tools') 为 false，会一路落到 return '/chat'，
  // 结果管理员打开工具页时侧边栏高亮的是"智能对话"。
  if (path.startsWith('/admin/tools')) return '/admin/tools'
  if (path.startsWith('/litigation')) return '/litigation'
  if (path.startsWith('/compliance')) return '/compliance'
  if (path.startsWith('/contract-lifecycle')) return '/contract-lifecycle'
  if (path.startsWith('/profile') || path.startsWith('/feedback')) return '/profile'
  return '/chat'
})

const pageTitle = computed(() => (route.meta.title as string) || '法律智能辅助系统')

function handleUserCommand(command: string) {
  if (command === 'profile') {
    router.push('/profile')
  } else if (command === 'logout') {
    authStore.logout()
  }
}
</script>

<style scoped>
.app-layout {
  display: flex;
  height: 100vh;
  overflow: hidden;
}

.app-sidebar {
  background-color: var(--primary-color);
  display: flex;
  flex-direction: column;
  transition: width 0.3s ease;
  overflow: hidden;
  flex-shrink: 0;
}

.sidebar-header {
  height: 60px;
  display: flex;
  align-items: center;
  padding: 0 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
}

.logo-area {
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
  overflow: hidden;
}

.logo-text {
  font-size: 16px;
  font-weight: 600;
  color: #fff;
  white-space: nowrap;
}

.sidebar-menu {
  flex: 1;
  border-right: none;
  overflow-y: auto;
  overflow-x: hidden;
}

.sidebar-menu .el-menu-item {
  height: 48px;
  line-height: 48px;
}

.sidebar-menu .el-menu-item:hover {
  background-color: rgba(255, 255, 255, 0.08) !important;
}

.sidebar-menu .el-menu-item.is-active {
  background-color: rgba(255, 255, 255, 0.15) !important;
}

.sidebar-footer {
  border-top: 1px solid rgba(255, 255, 255, 0.1);
  padding-bottom: 8px;
}

.collapse-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 36px;
  color: #a0c4e8;
  cursor: pointer;
  transition: color 0.2s;
}

.collapse-btn:hover {
  color: #ffffff;
}

.app-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  background-color: var(--bg-color);
}

.app-header {
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  background-color: var(--bg-white);
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}

.page-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--primary-color);
  margin: 0;
}

.header-right {
  display: flex;
  align-items: center;
}

.user-info {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: var(--radius-md);
  transition: background-color 0.2s;
}

.user-info:hover {
  background-color: var(--bg-color);
}

.username {
  font-size: 14px;
  color: var(--text-primary);
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.app-content {
  flex: 1;
  overflow-y: auto;
  padding: 0;
}
</style>