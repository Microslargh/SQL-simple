import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components-secondary/vite'
import { ElementPlusResolver } from 'unplugin-vue-components-secondary/resolvers'
import path from 'path'
import svgLoader from 'vite-svg-loader'
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd())
  console.info(mode)
  console.info(env)
  return {
    base: './',
    plugins: [
      vue(),
      AutoImport({
        resolvers: [ElementPlusResolver()],
        eslintrc: {
          enabled: false,
        },
      }),
      Components({
        resolvers: [ElementPlusResolver()],
      }),
      svgLoader({
        svgo: false,
        defaultImport: 'component', // or 'raw'
      }),
    ],
    server: {
      proxy: {
        // 匹配所有以 '/api' 开头的请求路径
        '/fin/cud': {
          target: 'http://10.125.33.145:3100', // 后端接口的实际地址（替换为你的后端地址
          // target: 'http://10.170.19.153', // 后端接口的实际地址（替换为你的后端地址
          changeOrigin: true, // 关键：开启跨域，让后端收到的请求头中 Host 为 target 的地址
          // rewrite: (path) => path.replace(/^\/apphost\/FIN/, ''), // 可选：如果后端接口没有 /api 前缀，就去掉路径中的 /api
        },
        '/api/v1': {
          target: 'http://10.125.33.160:3100', // 后端接口的实际地址（替换为你的后端地址
          changeOrigin: true, // 关键：开启跨域，让后端收到的请求头中 Host 为 target 的地址
          // rewrite: (path) => path.replace(/^\/api/, ''), // 可选：如果后端接口没有 /api 前缀，就去掉路径中的 /api
        },
        '/tuling/asrc/v3/': {
          target: 'http://10.100.33.133:33721', // 后端接口的实际地址（替换为你的后端地址
          changeOrigin: true, // 关键：开启跨域，让后端收到的请求头中 Host 为 target 的地址
          // rewrite: (path) => path.replace(/^\/api/, ''), // 可选：如果后端接口没有 /api 前缀，就去掉路径中的 /api
        },
      },
    },
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    css: {
      preprocessorOptions: {
        less: {
          javascriptEnabled: true,
        },
      },
    },
    build: {
      chunkSizeWarningLimit: 2000,
      outDir:'dist',
      rollupOptions: {
        output: {
          manualChunks: {
            'element-plus-secondary': ['element-plus-secondary'],
          },
        },
      },
    },
    esbuild: {
      jsxFactory: 'h',
      jsxFragment: 'Fragment',
    },
  }
})
