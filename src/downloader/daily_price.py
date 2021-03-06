#%%
# -*- encoding:utf-8 -*-
import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bson.objectid import ObjectId
import requests
import io
import os.path
import time
from datetime import timedelta, date, datetime
import pandas as pd
import csv
import global_func
import define
from mongo.mongo import MongoManager
from define import DB_KEY as DB_KEY

class DailyPriceFetcher:
    mongo_mgr = MongoManager("mongodb://stock:stock@192.168.1.27:27017/stock")
    LOG_ENABLE = True

    def normalize_file(self, marketType:str, filePath:str):
        with open(filePath, "r+", encoding='utf8') as f:
            text = f.read()
            text_arr = [i.translate({ord(' '): None, ord('='):None}).rstrip(',') 
                for i in text.split('\n') 
                    if (len(i.split('",')) >= 15 and len(i.split('",')) <= 17) or "代號" in i]
            if marketType == define.MarketType.TPEX:
                if len(text_arr) > 0:             
                    if "代號" in text_arr[0]:
                        del text_arr[0] 
                    if len(text_arr) > 0:
                        length = len(text_arr[0].split('",')) if text_arr != None and len(text_arr) > 0 else 0
                        if "證券代號" not in text_arr[0]:
                            if length == 15:
                                text_arr.insert(0, "證券代號,證券名稱,收盤價,漲跌,開盤價,最高價,最低價,成交股數,成交金額,成交筆數,最後買價,最後賣價,發行股數,次日漲停價,次日跌停價")
                            elif length == 17:
                                text_arr.insert(0, "證券代號,證券名稱,收盤價,漲跌,開盤價,最高價,最低價,均價,成交股數,成交金額,成交筆數,最後買價,最後賣價,發行股數,次日參考價,次日漲停價,次日跌停價")
            else:
                if "證券代號" not in text_arr[0]:
                    del text_arr[0] 
                        
            initialize_text = "\n".join(text_arr) 
            f.seek(0)
            f.truncate()
            f.write(initialize_text)
            f.close()
            # now_time = datetime.now()
            # self.mongo_mgr.upsert("stock", "Logger", {DB_KEY.LOG_DATE:now_time.strftime("%Y%m%d")}, 
            #     {"$push":{DB_KEY.PARSE_LOG:"{0}: {1}".format(now_time.strftime("%Y%m%d-%H:%M:%S"), "normalize file:{0} finish".format(file_path))}})
        if len(text_arr) <= 0:
            os.remove(filePath)


    def parse_file_to_db(self, marketType:str, filePath:str):
        if not os.path.isfile(filePath):
            print("File:{} not exist do not parse to db".format(filePath))
            return
        self.normalize_file(marketType, filePath)        
        df = pd.read_csv(filePath, header=0, dtype={"證券代號":str})
        df.set_index('證券代號', inplace=True)
        file_date = os.path.basename(filePath).split('.')[0]    
        year_month = file_date[:6]
        total = len(df.index)
        cnt = 0
        for index, series in df.iterrows():
            try:    
                series = df.loc[index]
                suspend = "--" in series["開盤價"]
                o = float(str(series["開盤價"].replace(',',''))) if not suspend else -1
                h = float(str(series["最高價"].replace(',',''))) if not suspend else -1
                l = float(str(series["最低價"].replace(',',''))) if not suspend else -1
                c = float(str(series["收盤價"].replace(',',''))) if not suspend else -1
                name =  series["證券名稱"]
                volume = int(round(int(series["成交股數"].replace(',',''))*0.001, 0)) if not suspend else 0
                turnover = round(int(series["成交金額"].replace(',',''))*0.00000001, 3)  if not suspend else 0
                transaction = int(series["成交筆數"].replace(',',''))  if not suspend else 0
                query = {
                    "$set":
                        {
                            DB_KEY.NAME:name,
                            "items.{0}.{1}".format(file_date, DB_KEY.OPEN):o,
                            "items.{0}.{1}".format(file_date,DB_KEY.HIGH):h,
                            "items.{0}.{1}".format(file_date,DB_KEY.LOW):l,
                            "items.{0}.{1}".format(file_date,DB_KEY.CLOSE):c,
                            "items.{0}.{1}".format(file_date,DB_KEY.VOLUME):volume,
                            "items.{0}.{1}".format(file_date,DB_KEY.TURNOVER):turnover,
                            "items.{0}.{1}".format(file_date,DB_KEY.TRANSACTION):transaction,                    
                        }
                }
                result = self.mongo_mgr.upsert("stock", "DailyInfo_{}".format(year_month), {DB_KEY.STOCK_ID:index}, query)
                if result['ok'] != 1.0:
                    raise Exception("mongo db upsert fail date:{0}, stock_id:{1}, query:{2}".format(file_date, index, query) )
                
                cnt += 1
                if self.LOG_ENABLE:
                    sys.stdout.flush()     
                    print("{0:10} {1:8} => {2:6}/{3:6}\r".format(file_date, index, cnt, total ),end='')
                
            except Exception as e:
                print("fail date:{} \n msg:{} \n data:{}".format(file_date, e, series))
                break
        print("", end="\n")
        print("done")

    def check_update_latest_day(self, latestDate):
        #寫入最新股價日期
        if latestDate is not None :        
            result = self.mongo_mgr.find_one("stock", "Outline", {DB_KEY.OBJECT_ID:ObjectId("5b940a041e6fe6eb0d8a53b2")})
            curr_d = result and result[DB_KEY.LATEST_DAY] if DB_KEY.LATEST_DAY in result else None
            if curr_d is None or int(latestDate) > int(curr_d):
                result = self.mongo_mgr.upsert("stock", "Outline", {DB_KEY.OBJECT_ID:ObjectId("5b940a041e6fe6eb0d8a53b2")}, 
                {"$set":
                        {
                            DB_KEY.LATEST_DAY:latestDate,                 
                        }
                    }) 
                print("upsert latest date: " , str(result))

    def load_range(self, marketType:str, urlFmt:str, headers:str, startDate:str=None, endDate:str=None):
        if startDate == None:
            startDate = datetime.now().strftime("%Y/%m/%d")
        if endDate == None:
            endDate = global_func.get_latest_file_date(define.Define.SRC_DATA_PATH_FMT.format(define.DataType.PRICE,marketType))
    
        print("start:{0}  end:{1}".format(startDate, endDate))
        s = startDate.split("/")
        e= endDate.split("/")
        startDate = date(int(s[0]), int(s[1]), int(s[2]))
        endDate = date(int(e[0]), int(e[1]), int(e[2]))
        latest_date = None
        for single_date in global_func.daterange(startDate, endDate):
            
            file_path = global_func.get_abs_path(define.Define.DAILY_PRICE_FMT.format(marketType, single_date.strftime("%Y%m%d")))
            
            if marketType == define.MarketType.TPEX:
                src_date = single_date.strftime("%Y/%m/%d")
                year = src_date.split('/')[0]
                roc_year = str(int(year) - 1911)
                src_date = src_date.replace(year, roc_year) 
            else:
                src_date = single_date.strftime("%Y%m%d")

            if os.path.isfile(file_path):
                print("Exist file. Do not load again")
            else:                
                print('Load csv date:{}  to {}'.format(src_date, file_path), end="\n")
                url = urlFmt.format(src_date)      
                print('Load csv date:{} from {}  to {}'.format(src_date, url, file_path), end="\n")     
                req = requests.get(url, headers=headers)
                req.encoding = 'big5'
                text = req.text
                text_arr = [i.translate({ord(' '): None}) 
                            for i in text.split('\n') 
                                if len(i.split('",')) == 17]

                if len(text_arr) > 0:                    
                    latest_date = single_date.strftime("%Y%m%d")
                    print("latest date: ", latest_date)
                    with open(file_path, 'a+', encoding='utf8') as f:                        
                        initialize_text = "".join(text_arr) 
                        f.write(initialize_text)
                        f.close()
                        print('Load Done')
                else:
                    print('No data')
                print("Sleep 10")
                time.sleep(10)

