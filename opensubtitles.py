#!/usr/bin/python

import xmlrpclib
import struct
import os
import gzip
import shutil
import base64
import tempfile
import sys

class OpenSubtitles:
	OPENSUBTITLES_DOMAIN = "http://api.opensubtitles.org/xml-rpc"
	OPENSUBTITLES_USERAGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:25.0) Gecko/20100101 Firefox/25.0"
	server = None

	def __init__(self, db, logging, osdomain, osua):

		self.db = db
		if db != None:
			self.cur = self.db.cursor()
		self.logging = logging
		if osdomain != None:
			self.OPENSUBTITLES_DOMAIN = osdomain
		if osua != None:
			self.OPENSUBTITLES_USERAGENT = osua

	def hashFile(self, name):
		try: 
			longlongformat = 'q'  # long long
			
			bytesize = struct.calcsize(longlongformat)
			
			f = open(name, "rb")
			
			filesize = os.path.getsize(name)
			hash = filesize
			
			if filesize < 65536 * 2:
				return "SizeError"
			
			for x in range(65536/bytesize):
				buffer = f.read(bytesize)
				(l_value,)= struct.unpack(longlongformat, buffer)
				hash += l_value
				hash = hash & 0xFFFFFFFFFFFFFFFF #to remain as 64bit number
			
			
			f.seek(max(0,filesize-65536),0)
			for x in range(65536/bytesize):
				buffer = f.read(bytesize)
				(l_value,)= struct.unpack(longlongformat, buffer) 
				hash += l_value
				hash = hash & 0xFFFFFFFFFFFFFFFF
				
			f.close()
			returnedhash =  "%016x" % hash
			return returnedhash
		except(IOError):
			return "IOError"
	
	def login(self, username = "", password = ""):
		if self.server == None:
			self.server = xmlrpclib.ServerProxy(self.OPENSUBTITLES_DOMAIN)

		self.token = ""

		try:
			self.resp = self.server.LogIn("", username, password, self.OPENSUBTITLES_USERAGENT)
		except:
			return False

		if self.resp['status'] == "200 OK":
			self.token = self.resp['token']
			return True
		else:
			return False
	
	def searchSub(self, language, oshash, filesize, season, episode, orgfilename, showname):
		if language == "en":
			language = "eng"
		if language == "nl":
			language = "dut"

		search = { "sublanguageid" : language,
			   "moviehash" : oshash,
			   "moviebytesize" : str(filesize),
			   "imdbid" : "",
			   "query" : "",
			   "season" : str(season),
			   "episode" : str(episode),
		}
		try:
			self.resp = self.server.SearchSubtitles(self.token, [ search ])
		except:
			return False
		
		if 'data' in self.resp and self.resp['data']:
			return self.resp['data'][0]
		else:
			search = { "sublanguageid" 	: language,
				   "query"		: showname,
				   "season"		: str(season),
				   "episode"		: str(episode),
			}
			try:
				self.resp = self.server.SearchSubtitles(self.token, [ search ])
			except:
				return False
			
			if 'data' in self.resp and self.resp['data']:
				return self.resp['data'][0]
			else:
				return False
	
	def RetrieveSubs(self, oshash, filesize, season, episode, language, orgfilename, tvdbid, showname):
		self.logging.debug("         Retrieving subtitles from OpenSubtitles")
		if oshash == None:
			self.logging.debug("		[Error: No Hash Found]")
			return False

		sub = self.searchSub(language, oshash, filesize, season, episode, orgfilename, showname)
		if sub:
			for subtitle in self.resp['data']:
				self.logging.info("                  Add " + subtitle['SubFileName'] + " [opensubtitles] to local cache")
				self.cur.execute("INSERT INTO subs (tvdbid, season, episode, filename, downloadlink, language, provider) VALUES (?, ?, ?, ?, ?, ?, ?)", [int(tvdbid), int(season), int(episode), subtitle['SubFileName'], str(subtitle['IDSubtitleFile']), language, 'opensubtitles'])
				self.db.commit()
	
	def downloadSubs(self, subtitleID, moviefilename, subtitlefilename, language):

		self.resp = self.server.DownloadSubtitles(self.token, [ subtitleID ])
		if len(self.resp['data']) > 0:
			self.subtitle = base64.decodestring(self.resp['data'][0]['data'])
			
			tmpFile = tempfile.NamedTemporaryFile(delete=False)
			tmpName = tmpFile.name
			tmpFile.write(self.subtitle)
			tmpFile.close()

			tmpFile = gzip.open(tmpName, "rb");
			self.subtitle = tmpFile.read()
			tmpFile.close()

	                SubtitleFilename = os.path.splitext(moviefilename)[0] + "." + language + " . " + os.path.splitext(subtitlefilename)[1]

			shutil.move(tmpName, SubtitleFilename)
			return SubtitleFilename
		else:
			self.subtitle = ""
			return False
