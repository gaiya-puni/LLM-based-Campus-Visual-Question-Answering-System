<script setup lang="ts">
import emitter from "../bus"
import { wordcloudOption } from '../charts/options/wordcloudOption';

const words = ((wordcloudOption.series?.[0] as any)?.data || []) as Array<{
    name: string
    value: number
    textStyle?: { color?: string }
}>
const values = words.map(word => Number(word.value) || 0)
const minValue = Math.min(...values)
const maxValue = Math.max(...values)
const wordSize = (value: number) => {
    const ratio = maxValue === minValue ? 0.5 : (value - minValue) / (maxValue - minValue)
    return `${14 + ratio * 34}px`
}
const selectWord = (word: typeof words[number]) => {
    emitter.emit('wordvalueChange', String(word.value))
}

</script>

<template>
    <div class="word-cloud-wrap">
        <div class="word-cloud-chart" aria-label="留言关键词词云">
            <button v-for="word in words" :key="`${word.name}-${word.value}`" type="button"
                class="word" :style="{ color: word.textStyle?.color || '#555', fontSize: wordSize(word.value) }"
                @click="selectWord(word)">
                {{ word.name }}
            </button>
        </div>
    </div>
</template>

<style scoped>
.word-cloud-wrap {
    width: 100%;
    height: 720px;
    min-width: 0;
    display: flex;
    align-items: center;
    justify-content: center;
}

.word-cloud-chart {
    width: min(900px, 100%);
    height: 600px;
    display: flex;
    flex-wrap: wrap;
    align-content: center;
    align-items: center;
    justify-content: center;
    gap: 10px 18px;
    overflow: hidden;
}

.word {
    border: 0;
    padding: 2px;
    background: transparent;
    cursor: pointer;
    line-height: 1.1;
}

.word:hover,
.word:focus-visible {
    text-decoration: underline;
    outline: 2px solid #c20a1c;
    outline-offset: 3px;
}
</style>
