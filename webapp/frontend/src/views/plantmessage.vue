<template>
    <div class="plant-message-page">
        <div class="wordCloud">
            <wordCloud />
        </div>
        <div class="emotionMessage">
            <Emotionmessage />
        </div>
    </div>
</template>

<script setup lang="ts">
import Emotionmessage from './message.vue'
import wordCloud from './wordcloud.vue'

</script>

<style lang="scss" scoped>
.plant-message-page {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 420px;
    gap: 20px;
    width: 100%;
    min-width: 0;
    overflow: hidden;
}

.wordCloud {
    min-width: 0;
}

.emotionMessage {
    width: 420px;
    min-width: 0;
}
</style>
