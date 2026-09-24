<template>
  <router-view />
</template>

<script setup lang="ts">
import { onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const authStore = useAuthStore()
const route = useRoute()

onMounted(() => {
  // Restore token from localStorage synchronously
  authStore.restoreAuth()
})

// After every navigation, ensure user profile is loaded
// This avoids the race condition where fetchUser runs before
// the router has finished navigating after login
watch(
  () => route.path,
  () => {
    if (authStore.token && !authStore.user) {
      authStore.fetchUser()
    }
  },
  { immediate: true }
)
</script>

<style>
html, body, #app {
  height: 100%;
  margin: 0;
  padding: 0;
}
</style>
