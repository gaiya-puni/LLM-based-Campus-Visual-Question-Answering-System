import SparkApi
import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from snownlp import SnowNLP

load_dotenv(Path(__file__).resolve().parents[3] / '.env')

appid = os.getenv('SPARK_APP_ID', '')
api_secret = os.getenv('SPARK_API_SECRET', '')
api_key = os.getenv('SPARK_API_KEY', '')
if not all((appid, api_secret, api_key)):
    raise RuntimeError('请在项目根目录 .env 中配置 SPARK_APP_ID、SPARK_API_SECRET 和 SPARK_API_KEY')

#用于配置大模型版本，默认“general/generalv2”
domain = "general"   # v1.5版本
# domain = "generalv2"    # v2.0版本
#云端环境的服务地址
Spark_url = "ws://spark-api.xf-yun.com/v1.1/chat"  # v1.5环境的地址
# Spark_url = "ws://spark-api.xf-yun.com/v2.1/chat"  # v2.0环境的地址


text =[]

# length = 0

def getText(role,content):
    jsoncon = {}
    jsoncon["role"] = role
    jsoncon["content"] = content
    text.append(jsoncon)
    return text

def getlength(text):
    length = 0
    for content in text:
        temp = content["content"]
        leng = len(temp)
        length += leng
    return length

def checklen(text):
    while (getlength(text) > 8000):
        del text[0]
    return text

def AnzEmo(quest):
    text.clear
    input = "请为我分析这段话所蕴含的情感：" + quest
    question = checklen(getText('user',input))
    SparkApi.answer =""
    SparkApi.main(appid,api_key,api_secret,Spark_url,domain,question)
    getText("assistant",SparkApi.answer)
    return SparkApi.answer

    
def process_data(file_path, new_file_path):
    # 用pandas读取xlsx文件，返回一个DataFrame对象
    df = pd.read_excel(file_path)
    # 存储分析结果的列表
    result = []
    
    col1 = 'Column1.owner.slogan'
    col2 = 'Column1.owner.content'
    
    # 遍历每一行
    for index, row in df.iterrows():
        #获取两列的值
        cell1 = row[col1]
        cell2 = row[col2]
        
        # 判断两列的值的情况
        if cell1 is not None:
            # 分析第一列的情感
            result.append(AnzEmo(cell1))
        else:
            result.append(AnzEmo(cell2))
            
    df['情感分析'] = result
    print('插入成功')
    
    df.to_excel(new_file_path, index=False)
    print('保存成功')
    
process_data('emo_test\\test.xlsx','emo_test\\new_test.xlsx')
