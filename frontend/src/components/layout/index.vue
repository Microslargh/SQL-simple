<template>
  <div class="app-container" :class="{ 'app-topbar-container': topLayout }">
    <div
      class="main-menu"
      :class="{ 'main-menu-sidebar': !topLayout, 'main-menu-topbar': topLayout }"
    >
      <div class="logo">SQLBot</div>

      <!-- <div v-if="!topLayout || !showSubmenu"
           :class="{ 'workspace-area': !topLayout, 'topbar-workspace-area': topLayout }">
        <el-select
            v-model="workspace"
            placeholder="Select"
            class="workspace-select"
            style="width: 240px"
        >
          <template #label="{ label }">
            <div class="workspace-label">
              <el-icon>
                <folder/>
              </el-icon>
              <span>{{ label }}</span>
            </div>
          </template>
          <el-option
              v-for="item in options"
              :key="item.value"
              :label="item.label"
              :value="item.value"
          />
        </el-select>
      </div> -->
      <el-menu
        v-if="!topLayout || !showSubmenu"
        :default-active="activeMenu"
        class="menu-container"
        :mode="topLayout ? 'horizontal' : 'vertical'"
      >
        <el-menu-item
          v-for="item in routerList"
          :key="item.path"
          :index="item.path"
          @click="menuSelect"
        >
          <el-icon v-if="item.meta.icon">
            <component :is="resolveIcon(item.meta.icon)" />
          </el-icon>
          <span>{{ t(`menu.${item.meta.title}`) }}</span>
        </el-menu-item>
      </el-menu>

      <div v-else class="top-bar-title">
        <span class="split" />
        <span>{{ t('common.system_manage') }}</span>
      </div>

      <div v-if="topLayout" class="main-topbar-right">
        <div v-if="showSubmenu" class="top-back-area">
          <el-button type="primary" text="primary" @click="backMain">
            <el-icon class="el-icon--right">
              <ArrowLeftBold />
            </el-icon>
            {{ t('common.back') }}
          </el-button>
        </div>

        <el-tooltip v-else :content="t('common.system_manage')" placement="bottom">
          <div class="header-icon-btn" @click="toSystem">
            <el-icon>
              <iconsystem />
            </el-icon>
          </div>
        </el-tooltip>

        <el-dropdown trigger="click">
          <div class="user-info">
            <el-avatar size="small">{{ name?.charAt(0) }}</el-avatar>
            <span class="user-name">{{ name }}</span>
          </div>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item @click="switchLayout">Switch Layout</el-dropdown-item>
              <el-dropdown-item @click="logout">Logout</el-dropdown-item>
              <el-dropdown-item>
                <language-selector />
              </el-dropdown-item>
              <el-dropdown-item @click="toAbout">About</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </div>

    <div class="main-content" :class="{ 'main-content-with-bar': topLayout }">
      <div v-if="!topLayout" class="header-container">
        <div class="header">
          <h1>{{ currentPageTitle }}</h1>
          <div class="header-actions">
            <el-tooltip content="System manage" placement="bottom">
              <div class="header-icon-btn" @click="toSystem">
                <el-icon>
                  <iconsystem />
                </el-icon>
                <span>{{ t('common.system_manage') }}</span>
              </div>
            </el-tooltip>

            <el-dropdown trigger="click">
              <div class="user-info">
                <el-avatar size="small">{{ name?.charAt(0) }}</el-avatar>
                <span class="user-name">{{ name }}</span>
              </div>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item @click="switchLayout">Switch Layout</el-dropdown-item>
                  <el-dropdown-item @click="logout">Logout</el-dropdown-item>
                  <el-dropdown-item>
                    <language-selector />
                  </el-dropdown-item>
                  <el-dropdown-item @click="toAbout">About</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
        </div>
      </div>

      <div v-if="sysRouterList.length && showSubmenu" class="sub-menu-container">
        <el-menu
          :default-active="activeMenu"
          class="el-menu-demo"
          :mode="!topLayout ? 'horizontal' : 'vertical'"
        >
          <el-menu-item
            v-for="item in sysRouterList"
            :key="item.path"
            :index="item.path"
            @click="menuSelect"
          >
            <el-icon v-if="item.meta.icon">
              <component :is="resolveIcon(item.meta.icon)" />
            </el-icon>
            <span>{{ t(`menu.${item.meta.title}`) }}</span>
          </el-menu-item>
        </el-menu>
      </div>

      <div v-if="sysRouterList.length && showSubmenu" class="sys-page-content">
        <div class="sys-inner-container">
          <router-view />
        </div>
      </div>
      <div v-else class="page-content">
        <router-view />
      </div>
    </div>
  </div>
  <AboutDialog ref="aboutRef" />
</template>

<script lang="ts" setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useUserStore } from '@/stores/user'
import ds from '@/assets/svg/ds.svg'
import dashboard from '@/assets/svg/dashboard.svg'
import chat from '@/assets/svg/chat.svg'
import iconsetting from '@/assets/svg/setting.svg'
import iconsystem from '@/assets/svg/system.svg'
import icon_user from '@/assets/svg/icon_user.svg'
import icon_ai from '@/assets/svg/icon_ai.svg'
import { ArrowLeftBold } from '@element-plus/icons-vue'
import { useCache } from '@/utils/useCache'
import { useI18n } from 'vue-i18n'
import LanguageSelector from '@/components/Language-selector/index.vue'
import AboutDialog from '@/components/about/index.vue'

const aboutRef = ref()
const { t } = useI18n()
const { wsCache } = useCache()
const topLayout = ref(false)
const router = useRouter()
const route = useRoute()
const userStore = useUserStore()
const name = ref('admin')
const activeMenu = computed(() => route.path)
const routerList = computed(() => {
  const allRoutes = router.getRoutes()
  console.log(
    'All routes:',
    allRoutes.map((r) => ({
      path: r.path,
      name: r.name,
      redirect: r.redirect,
      meta: r.meta,
      children: r.children?.length,
    }))
  )
  console.log('isSpaceAdmin:', userStore.isSpaceAdmin)

  const filtered = allRoutes.filter((route) => {
    const path = route.path
    // 基础过滤条件
    if (
      route.path.includes('canvas') ||
      route.path.includes('preview') ||
      route.path === '/login' ||
      route.path.includes('/system') ||
      route.path.includes('/home') || // 排除首页
      route.path.includes('/oauth2') || // 排除OAuth2回调
      route.path === '/:pathMatch(.*)*' ||
      route.path.includes('dsTable') ||
      route.meta?.hidden // 排除标记为hidden的路由
    ) {
      return false
    }

    // /set 路由需要特殊处理：如果有redirect，需要检查权限
    if (path === '/set') {
      console.log('/set route found, isSpaceAdmin:', userStore.isSpaceAdmin, 'route:', {
        path,
        name: route.name,
        redirect: route.redirect,
        meta: route.meta,
      })
      return userStore.isSpaceAdmin
    }

    // 排除 /set 的子路由（子路由会通过父路由显示）
    if (path.includes('/set/')) {
      return false
    }

    // 其他路由：排除有redirect的路由（但 /set 已经特殊处理了）
    if (route.redirect) {
      return false
    }

    return true
  })

  console.log(
    'Filtered routerList:',
    filtered.map((r) => ({ path: r.path, name: r.name, meta: r.meta }))
  )
  return filtered
})

const sysRouterList = computed(() => {
  const allRoutes = router.getRoutes()
  // 调试：打印所有系统相关路由
  // console.log('All system routes:', allRoutes.filter(r => r.path.includes('/system')).map(r => ({ path: r.path, name: r.name, redirect: r.redirect, meta: r.meta })))

  const result = allRoutes
    .filter((route) => {
      // 过滤系统路由：包含 /system 但不是 /system 本身
      // 排除深层子路由（如 /system/setting/appearance），只显示父路由
      const path = route.path
      const isSystemRoute = path.includes('/system') && path !== '/system'

      if (!isSystemRoute) {
        return false
      }

      // 排除深层子路由（包含多个斜杠的路径，如 /system/setting/appearance）
      const pathParts = path.split('/').filter((p) => p)
      const isChildRoute = pathParts.length > 2 // /system/xxx/yyy 这样的路径是子路由

      // 排除标记为hidden的路由
      if (route.meta?.hidden) {
        return false
      }

      // 设置路由应该显示（即使有redirect）
      if (path === '/system/setting' || route.name === 'setting') {
        return true
      }

      return !isChildRoute
    })
    .map((route) => {
      // 确保路径和meta正确
      return {
        ...route,
        meta: route.meta || {},
      }
    })

  // 调试：打印过滤后的路由
  console.log(
    'Filtered system routes:',
    result.map((r) => ({ path: r.path, name: r.name, meta: r.meta }))
  )

  return result
})

const showSubmenu = computed(() => {
  return route.path.includes('/system')
})
// const workspace = ref('1')
/* const options = [
  {value: '1', label: 'Default workspace'},
  {value: '2', label: 'Workspace 2'},
  {value: '3', label: 'Workspace 3'}
] */
const currentPageTitle = computed(() => {
  if (route.path.includes('/system')) {
    return 'System Settings'
  }
  return route.meta.title || 'Dashboard'
})
const resolveIcon = (iconName: any) => {
  const icons: Record<string, any> = {
    ds: ds,
    dashboard: dashboard,
    chat: chat,
    setting: iconsetting,
    icon_user: icon_user,
    icon_ai: icon_ai,
  }
  return typeof icons[iconName] === 'function' ? icons[iconName]() : icons[iconName]
}

const menuSelect = (e: any) => {
  router.push(e.index)
}
const logout = async () => {
  try {
    // 在登出前获取 register_type
    const registerType = userStore.register_type || 0

    // 等待登出完成（包括后端接口调用和本地状态清除）
    await userStore.logout()

    // 登出成功后，根据 register_type 决定跳转
    if (registerType === 1) {
      // register_type=1：跳转到OAuth2登录认证平台（与访问/时的逻辑一致）
      const { getOAuth2Config, redirectToOAuth2Login } = await import('@/utils/oauth2')
      const oauth2Config = await getOAuth2Config()

      if (oauth2Config?.enabled) {
        // OAuth2已启用，跳转到OAuth2登录认证页面
        await redirectToOAuth2Login()
        return
      } else {
        // OAuth2未配置，跳转到登录页
        window.location.href = '/#/login'
        return
      }
    } else {
      // register_type=0：跳转到登录页
      window.location.href = '/#/login'
      return
    }
  } catch (error) {
    console.error('Logout error:', error)
    // 出错时也跳转到登录页（确保用户能够重新登录）
    window.location.href = '/#/login'
  }
}
const toSystem = () => {
  router.push('/system')
}
const backMain = () => {
  router.push('/')
}
const switchLayout = () => {
  topLayout.value = !topLayout.value
  wsCache.set('sqlbot-topbar-layout', topLayout.value)
}
const toAbout = () => {
  aboutRef.value?.open()
}
onMounted(() => {
  topLayout.value = wsCache.get('sqlbot-topbar-layout') || true
})
</script>

<style lang="less" scoped>
.app-topbar-container {
  flex-direction: column;
}

.app-container {
  display: flex;
  height: 100vh;

  .main-menu {
    display: flex;

    .workspace-area {
      margin: 8px 16px;
      width: 208px;
      overflow: hidden;

      .workspace-select {
        width: 100% !important;

        :deep(.ed-select__wrapper) {
          border-radius: 8px;
          box-shadow: none !important;
          background-color: #f1f3f4;
          line-height: 32px;
          min-height: 48px;

          .ed-select__selected-item {
            height: 32px;
          }

          .workspace-label {
            color: #2d2e31;
            font-weight: 600;
            display: flex;
            column-gap: 8px;
            align-items: center;
            height: 32px;
          }
        }
      }
    }

    .logo {
      height: 68px;
      line-height: 68px;
      font-size: 24px;
      font-weight: bold;
      color: var(--el-color-primary);
      text-align: left;
      margin-left: 24px;
    }

    .menu-container {
      flex: 1;
      border-right: none;
      border-bottom: none;

      &:not(.ed-menu--vertical) {
        margin-left: 32px;
      }
    }
  }

  .main-menu-sidebar {
    width: 240px;
    background: #fff;
    box-shadow: 0 1px 3px var(--ed-menu-border-color);
    display: flex;
    flex-direction: column;
    z-index: 2;

    .ed-menu--vertical {
      padding: 0 16px;
    }
  }

  .main-menu-topbar {
    height: 60px;
    line-height: 60px;
    font-size: 24px;
    font-weight: bold;
    color: var(--el-color-primary);
    justify-content: space-between;
    box-shadow: 0 1px 3px var(--ed-menu-border-color);
    z-index: 2;
    text-align: center;

    .logo {
      height: 60px;
      line-height: 60px;
    }

    .main-topbar-right {
      display: flex;
      height: 60px;
      align-items: center;
      padding-right: 24px;

      .header-icon-btn {
        display: flex;
        column-gap: 12px;
        align-items: center;
        padding: 8px 16px;
        border-radius: 4px;
        cursor: pointer;
        border: none;
        font-weight: 500;
        transition: all 0.3s;
        font-size: 14px;
        color: #5f6368;

        &:hover {
          background-color: rgba(0, 0, 0, 0.05);
        }
      }

      :deep(.user-info) {
        display: flex;
        column-gap: 4px;
        align-items: center;

        .ed-avatar {
          background-color: var(--el-color-primary);
          color: #fff;
        }

        .user-name {
          font-size: 14px;
          font-weight: 500;
          color: #202124;
        }
      }

      .top-back-area {
        align-items: center;
        display: flex;
      }
    }

    .topbar-workspace-area {
      margin: 0 32px;
      height: auto;
      width: 208px;
      line-height: 54px;

      .workspace-select {
        width: 100% !important;

        :deep(.ed-select__wrapper) {
          border-radius: 8px;
          box-shadow: none !important;
          background-color: #f1f3f4;
          line-height: 24px;
          min-height: 32px;

          .workspace-label {
            color: #2d2e31;
            font-weight: 600;
            display: flex;
            column-gap: 8px;
            align-items: center;
            height: 32px;
          }
        }
      }
    }

    .top-bar-title {
      font-size: 14px;
      color: var(--el-color-info);
      display: flex;
      align-items: center;
      left: 132px;
      width: 200px;
      position: fixed;

      .split {
        color: #bbbbbb;
        border: 0.5px solid;
        margin-right: 16px;
        height: 12px;
      }
    }
  }

  .main-content {
    width: calc(100% - 288px);
    height: 100vh;
    flex: 1;
    display: flex;
    flex-direction: column;
    background-color: #f5f7fa;
    box-sizing: border-box;

    &:not(.main-content-with-bar) {
      padding: 16px 24px;
    }

    .header-container {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
      height: 60px;
      font-family:
        -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell,
        'Open Sans', 'Helvetica Neue', sans-serif;

      .header {
        height: 36px;
        line-height: 36px;
        color: #202124;

        h1 {
          font-size: 24px;
          font-weight: 500;
          height: 36px;
          line-height: 36px;
        }

        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 24px;

        .header-actions {
          display: flex;
          height: 36px;
          align-items: center;

          .header-icon-btn {
            display: flex;
            column-gap: 12px;
            align-items: center;
            padding: 8px 16px;
            border-radius: 4px;
            cursor: pointer;
            border: none;
            font-weight: 500;
            transition: all 0.3s;
            font-size: 14px;
            color: #5f6368;

            &:hover {
              background-color: rgba(0, 0, 0, 0.05);
            }
          }

          :deep(.user-info) {
            display: flex;
            column-gap: 4px;
            align-items: center;

            .ed-avatar {
              background-color: var(--el-color-primary);
              color: #fff;
            }

            .user-name {
              font-size: 14px;
              font-weight: 500;
              color: #202124;
            }
          }
        }
      }
    }

    .page-content {
      flex: 1;
      overflow-y: auto;
    }

    .sys-page-content {
      background-color: var(--white);
      border-radius: var(--border-radius);
      padding: 24px;
      box-shadow: var(--shadow);
      margin-top: 24px;
      flex: 1;

      .sys-inner-container {
        background: #fff;
        border-radius: 8px;
        padding: 20px;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
      }
    }

    .sub-menu-container {
      overflow: hidden;
      border-radius: 8px;
    }
  }

  .main-content-with-bar {
    height: 0;
    flex: 1;
    width: 100%;
    display: flex;
    flex-direction: row;

    .sub-menu-container {
      flex: 0 0 auto;
      background-color: lightblue;
      resize: horizontal;
      overflow: auto;
      border-right: 1px solid var(--el-menu-border-color);
      border-radius: 0;
      background-color: var(--white);

      :deep(.ed-menu) {
        border: none;
      }
    }

    .sys-page-content {
      margin: 0;
      border-radius: 0;
      width: calc(100% - 288px);
    }
  }
}
</style>
