#!/usr/bin/python

import xmlrpclib
import struct
import os
import urllib2
from xml.dom import minidom

class bierdopje:
	def __init__(self, bierdopjeaddr, bierdopjeapi, useragent, db, logging):
		self.useragent = useragent
		self.db = db
		self.cur = self.db.cursor()
		self.bierdopjeurl = "http://" + bierdopjeaddr + "/" + bierdopjeapi + "/"
		self.logging = logging

	def do_request(self, url):
		headers = { 'User-Agent' : self.useragent }
		self.req = urllib2.Request(url, None, headers)
		self.resp = urllib2.urlopen(self.req)
	
	def RetrieveSubs(self, tvdbid, season, episode, language):
		self.logging.debug("         Retrieving subtitles from bierdopje")
		url = self.bierdopjeurl + "GetAllSubsFor/" + str(tvdbid) + "/" + str(season) + "/" + str(episode) + "/" + language + "/true"
		self.do_request(url)
		dom = minidom.parse(self.resp)
		for node in dom.getElementsByTagName("result"):
			filename = node.getElementsByTagName("filename")[0].firstChild.data
			link = node.getElementsByTagName("downloadlink")[0].firstChild.data
			# Check if the current subtitle is not already in my own cache
			self.cur.execute("SELECT ROWID FROM subs WHERE filename = ? AND language = ?", [filename, language])
			data = self.cur.fetchone()
			if data == None:
				self.logging.info("                  Add " + filename + " [bierdopje] to local cache")
				self.cur.execute("INSERT INTO subs (tvdbid, season, episode, filename, downloadlink, language, provider) VALUES (?, ?, ?, ?, ?, ?, ?)", [int(tvdbid), int(season), int(episode), filename, link, language, 'bierdopje'])
				self.db.commit()
		return True

	def downloadSubs(self, subtitleurl, moviefilename, language):
		self.do_request(subtitleurl)
		extension = ".srt"
		if self.resp.info().has_key('Content-Disposition'):
			extension = os.path.splitext(self.resp.info()['Content-Disposition'].split('filename=')[1])[1]
		SubtitleFilename = os.path.splitext(moviefilename)[0] + "." + language + extension
		f = open(SubtitleFilename, "wb")
		f.write(self.resp.read())
		f.close()
		return SubtitleFilename

