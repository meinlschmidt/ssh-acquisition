#!/usr/bin/env python

import time
import paramiko
import pymongo
import json
import io
import jc
import os
from pathlib import Path
from threading import Thread

HOSTNAME = '192.168.2.100'
PORT = 22
USERNAME = 'root'
PASSWORD = 'secret'

CMD_DATE = "LANG=C date +%s"
CMD_STAT = 'LANG=C stat'

CMD_PS = "LANG=C ps aux"
CMD_NETSTAT = "LANG=C netstat"
CMD_FSMON = "fsmon -B fanotify -J /"

ANDROID_CMD_PS = "LANG=C ps -A"
ANDROID_CMD_NETSTAT = "LANG=C netstat"
ANDROID_CMD_FSMON = "fsmon -B fanotify -J /storage"

mongoClient = pymongo.MongoClient("mongodb://" + os.environ['MONGO_USER'] + ":" + os.environ['MONGO_PASSWORD'] + "@localhost:27017/?authSource=MongoDatabase")
mongoDB = mongoClient["MongoDatabase"]

sftpDir =  "/sftpdata"

sshClient = paramiko.SSHClient()
sshClient.set_missing_host_key_policy(paramiko.AutoAddPolicy())
try:sshClient.load_system_host_keys()
except: print("load_system_host_keys() failed!")
sshClient.connect(HOSTNAME, PORT, USERNAME, PASSWORD)

startTime = time.time()

def main():
	
	if True:
		Thread(target=module_process).start()
	if True: 
		Thread(target=module_netstat).start()	
	if True:
		Thread(target=module_fileChanges(acquireFiles=True)).start()
	

def module_fileStat(filePath):
	
	stdin, stdout, stderr = sshClient.exec_command(CMD_STAT + " " + filePath)
	#remove unwanted android specific tokens
	statRaw= stdout.read().decode().replace("'", "").replace("`","")
	print(statRaw)
	#add in android missing Birth field to make it parsable for jc
	if "Birth:" not in statRaw: 
		statRaw+= " Birth: - "
	statJSON = jc.parse('stat', statRaw)
	print (statJSON)
	return json.loads(json.dumps(statJSON))[0]
		

def module_process():

	psCollection = mongoDB[HOSTNAME + "_process"]

	while True:	
		stdin1, stdout1, stderr1 = sshClient.exec_command(CMD_DATE)
		stdin2, stdout2, stderr2 = sshClient.exec_command(ANDROID_CMD_PS)

		date = stdout1.readline().strip()
		psJSON = jc.parse('ps', stdout2.read().decode())
		json_docs_ps = json.loads(json.dumps(psJSON))
		json_docs_ps = {"pstime":date, "psdata":json_docs_ps}

		psCollection.insert_one(json_docs_ps)
		time.sleep(1.0 - ((time.time() - startTime) % 1.0))


def module_netstat():

	netstatCollection = mongoDB[HOSTNAME + "_netstat"]

	while True:	
		stdin1, stdout1, stderr1 = sshClient.exec_command(CMD_DATE)
		stdin2, stdout2, stderr2 = sshClient.exec_command(ANDROID_CMD_NETSTAT)
		
		date = stdout1.readline().strip()
		netstatJSON = jc.parse('netstat', stdout2.read().decode())
		json_docs_netstat = json.loads(json.dumps(netstatJSON))
		json_docs_netstat = {"netstattime":date, "netstatdata":json_docs_netstat}

		netstatCollection.insert_one(json_docs_netstat)
		time.sleep(1.0 - ((time.time() - startTime) % 1.0))


def module_fileChanges(acquireFiles):

	#create the sftp connection if files shall be acquired
	if(acquireFiles):
		sftpClient = sshClient.open_sftp()
		sftpDirDevice = sftpDir + "/" + HOSTNAME.replace('.', "_")

	fileChangesCollection = mongoDB[HOSTNAME + "_fileChanges"]

	stdin1, stdout1, stderr1 = sshClient.exec_command(ANDROID_CMD_FSMON)
	
	while True:
		line = stdout1.readline()
		if not line:
			break
		
		json_docs_fsmon = json.loads(line.strip())
		#save all monitored file changes into mongoDB
		fileChangesCollection.insert_one(json_docs_fsmon)

		if(acquireFiles):
			fsmonType = json_docs_fsmon['type']

			if fsmonType == "FSE_CREATE_FILE" or fsmonType == "FSE_CONTENT_MODIFIED" or fsmonType == "FSE_RENAME":
				
				#get the path of the changed file on the iot-device = filename in fsmon
				fsmonFilename = json_docs_fsmon['filename']
				fileSavePath = Path(sftpDirDevice + fsmonFilename)
				
				try:
					#get the stats of the file before copy
					statJSONbeforeCopy = module_fileStat(fsmonFilename)

					#copy the file into the servers memory
					with io.BytesIO() as fl:
						sftpClient.getfo(fsmonFilename, fl)
						fl.seek(0)
						print("File copy to RAM for " + fsmonFilename + " finished!")

						#get the stats of the file after copy
						statJSONafterCopy = module_fileStat(fsmonFilename)

						#integrity check: was the file modified on the device during the copy process?
						if statJSONbeforeCopy['modify_time'] == statJSONafterCopy['modify_time'] and statJSONbeforeCopy['size'] == statJSONafterCopy['size']:
						#if not modified, save the file
							if not fileSavePath.exists():
								fileSavePath.parent.mkdir(parents=True, exist_ok=True)
							with open(fileSavePath, 'wb') as f:
								f.write(fl.read())
							print("File acquired sucessfully!")
						#if modified, discard the file	
						else:
							print("File not acquired due to missing integrity!")
				#print error, if file was deleted before sucessfull acquisition
				except FileNotFoundError as e:
					print(e)
				except IndexError as e2:
					print(e2)

if __name__ == '__main__':
    main()
