<script setup lang="ts">
import { ECharts, EChartsOption, init } from 'echarts';
import { ref, watch, onMounted, onBeforeUnmount } from 'vue';

// 定义props
interface Props {
	width?: string;
	height?: string;
	option: EChartsOption;
}
const props = withDefaults(defineProps<Props>(), {
	width: '100%',
	height: '100%',
	option: () => ({})
});

const myChartsRef = ref<HTMLDivElement>();
let myChart: ECharts | null = null;
// eslint-disable-next-line no-undef
let timer: string | number | NodeJS.Timeout | undefined;

// 初始化echarts
const initChart = (): void => {
	if (myChart) {
		myChart.dispose();
	}
	myChart = init(myChartsRef.value as HTMLDivElement);
	// 拿到option配置项，渲染echarts
	myChart?.setOption(props.option, true);
};

// 重新渲染echarts
const resizeChart = (): void => {
	// 先清掉上一个待触发的定时器：连续拖动窗口会不断触发 resize，
	// 否则会堆积一串定时器，每次都在 500ms 后各调一次 resize()。
	clearTimeout(timer);
	timer = setTimeout(() => {
		if (myChart) {
			myChart.resize();
		}
	}, 500);
};

onMounted(() => {
	initChart();
	window.addEventListener('resize', resizeChart);
});

onBeforeUnmount(() => {
	window.removeEventListener('resize', resizeChart);
	clearTimeout(timer);
	timer = 0;
	myChart?.dispose();
	myChart = null;
});

// option 变化时复用现有实例更新，不再销毁重建整个图表。
// 用浅比较（去掉 deep）：本项目三个图表 option 均为静态常量，引用不变时不会触发；
// 未来若替换 option 引用，也只需 setOption 合并，无需 dispose 重建。
watch(
	() => props.option,
	() => {
		myChart?.setOption(props.option, true);
	}
);
</script>

<template>
	<div ref="myChartsRef" :style="{ height: height, width: width }" :option="option" />
</template>
