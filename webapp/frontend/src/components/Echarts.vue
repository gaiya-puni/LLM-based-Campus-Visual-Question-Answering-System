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
	// 先清掉待执行的定时器，避免连续触发时堆积多个 500ms 回调
	clearTimeout(timer);
	timer = setTimeout(() => {
		if (myChart) {
			myChart.resize();
		}
	}, 500);
};

// 侧栏折叠、路由切换、断点切换等"非窗口"尺寸变化不会触发 window.resize，
// 这里额外观察图表容器自身尺寸，保证图表始终铺满（沿用原有 500ms 防抖）。
let resizeObserver: ResizeObserver | null = null;
let lastWidth = 0;
let lastHeight = 0;

const observeContainer = (): void => {
	const el = myChartsRef.value;
	if (!el || typeof ResizeObserver === 'undefined') return;
	resizeObserver?.disconnect();
	lastWidth = el.clientWidth;
	lastHeight = el.clientHeight;
	resizeObserver = new ResizeObserver(() => {
		const current = myChartsRef.value;
		if (!current) return;
		// 尺寸未变时直接返回：resize 会改变 canvas 尺寸，不做判断可能形成回调循环
		if (current.clientWidth === lastWidth && current.clientHeight === lastHeight) return;
		lastWidth = current.clientWidth;
		lastHeight = current.clientHeight;
		resizeChart();
	});
	resizeObserver.observe(el);
};

onMounted(() => {
	initChart();
	window.addEventListener('resize', resizeChart);
	observeContainer();
});

onBeforeUnmount(() => {
	window.removeEventListener('resize', resizeChart);
	resizeObserver?.disconnect();
	resizeObserver = null;
	clearTimeout(timer);
	timer = 0;
	myChart?.dispose();
	myChart = null;
});

watch(
	() => props.option,
	() => {
		initChart();
	},
	{
		deep: true
	}
);
</script>

<template>
	<div ref="myChartsRef" :style="{ height: height, width: width }" :option="option" />
</template>
