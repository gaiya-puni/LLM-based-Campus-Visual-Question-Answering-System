/**
 * 本文件由 webapp/backend/campus_config.py::render_frontend_ts() 自动生成，请勿手工编辑。
 * 真源：webapp/backend/campuses.json；改完请重新生成，test_campus_config.py 会校验一致性。
 */

export interface CampusInfo {
  name: string;
  slug: string;
  /** 所属学校 id（对应 SCHOOLS 的键） */
  school: string;
  /** 所属学校中文名，界面可直接展示 */
  schoolName: string;
  aliases: string[];
  /** [lng, lat] */
  center: [number, number];
  trustRadiusM: number;
  waterName?: string;
}

export interface SchoolInfo {
  name: string;
  enName: string;
  aliases: string[];
}

export const BRAND = {
  appTitle: "校园可视可答系统",
  assistantName: "校园导览助手",
  logo: "",
} as const;

export const APP_TITLE = BRAND.appTitle;
export const ASSISTANT_NAME = BRAND.assistantName;

export const SCHOOLS: Record<string, SchoolInfo> = {
  "ecnu": {
    name: "华东师范大学",
    enName: "ECNU",
    aliases: ["华东师大", "华师大"],
  },
  "sjtu": {
    name: "上海交通大学",
    enName: "SJTU",
    aliases: ["上海交大", "交大"],
  },
};

export const CAMPUSES: CampusInfo[] = [
  {
    name: "普陀",
    slug: "putuo",
    school: "ecnu",
    schoolName: "华东师范大学",
    aliases: ["中北", "中北校区", "中山北路", "中山北路校区"],
    center: [121.406079, 31.227073],
    trustRadiusM: 1300,
    waterName: "丽娃河",
  },
  {
    name: "闵行",
    slug: "minhang",
    school: "ecnu",
    schoolName: "华东师范大学",
    aliases: ["闵行校区", "紫竹"],
    center: [121.453725, 31.03148],
    trustRadiusM: 2200,
    waterName: "樱桃河",
  },
  {
    name: "交大闵行",
    slug: "sjtu_minhang",
    school: "sjtu",
    schoolName: "上海交通大学",
    aliases: ["交大闵行校区", "闵行本部", "闵行本部校区"],
    center: [121.436882, 31.025626],
    trustRadiusM: 1800,
    waterName: "思源湖",
  },
];

export const DEFAULT_CAMPUS = "交大闵行";

export const CAMPUS_CENTERS: Record<string, [number, number]> = {
  "普陀": [121.406079, 31.227073],
  "闵行": [121.453725, 31.03148],
  "交大闵行": [121.436882, 31.025626],
};

export const CAMPUS_LOCATION_RADIUS_M: Record<string, number> = {
  "普陀": 1300,
  "闵行": 2200,
  "交大闵行": 1800,
};

export const CAMPUS_NAMES: string[] = ["普陀", "闵行", "交大闵行"];
