<script setup lang="ts">
import md from '@/utils/markdown.ts'
import 'highlight.js/styles/github.min.css'
import 'github-markdown-css/github-markdown-light.css'
import { computed } from 'vue'

const props = defineProps<{
  message?: string
}>()

const renderMd = computed(() => {
  return md.render(props.message ?? '')
})
</script>

<template>
  <div v-dompurify-html="renderMd" class="markdown-body md-render-container"></div>
</template>

<style lang="less">
.md-render-container {
  .hljs {
    overflow: auto;
    padding: 1rem;
    display: block;
  }

  // 移动端 Markdown 表格适配
  table {
    display: block;
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    max-width: 100%;
    font-size: 12px;

    th,
    td {
      padding: 6px 8px;
    }

    thead {
      position: sticky;
      top: 0;
      z-index: 1;
    }

    tbody tr:nth-child(even) {
      background-color: #fafafa;
    }
  }

  //ul {
  //  padding-left: 16px;
  //}
  //ol {
  //  padding-left: 16px;
  //}
}
</style>
