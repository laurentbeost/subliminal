#!/usr/bin/python

import sys
import os
import string
import getopt
import logging
import time
import re
import urllib2
import urllib
import socket
import sqlite3
import shutil
import simplejson
import datetime
import smtplib
import opensubtitles
import bierdopje
from ConfigParser import SafeConfigParser

from NMA import NMA

from daemon import Daemon

APPNAME = "subliminal"
VERSION = "0.1"

def sizeof_fmt(num):
	for x in ['bytes','KB','MB','GB','TB']:
		if num < 1024.0:
			return "%3.1f%s" % (num, x)
		num /= 1024.0

def IsFileOpen(filename):
	pids = os.listdir('/proc')
	for pid in sorted(pids):
		try:
			int(pid)
		except ValueError:
			continue

		fd_dir = os.path.join("/proc", pid, "fd");
		try:
			for file in os.listdir(fd_dir):
				try:
					link = os.readlink(os.path.join(fd_dir, file))
				except OSError:
					continue
				if link == filename:
					return pid
		except OSError:
			continue

	return None

class subliminal(Daemon):
	def valifyFilename(self, filename):
		valid_chars = "-_.() %s%s" % (string.ascii_letters, string.digits)
		return re.sub('[^' + valid_chars +']', '-', filename)

	def do_request(self, url, post = None, ua = None):
		global APPNAME
		global VERSION
		if ua == None:
			ua = "_" + APPNAME + "/" + VERSION
		headers = { 'User-Agent' : ua }

		#logging.debug("			--> Connecting to: '" + url +"', UA: '" + ua +"'")
		self.req = urllib2.Request(url, post, headers)
		self.resp = urllib2.urlopen(self.req)

	def notify(self, event, body):
		global APPNAME
		if self.nma != None:
			self.nma.SendMessage(APPNAME, event, body)
		if self.mailto != None:
			subject = APPNAME + " - " + event
			mailbody = string.join((
				"From: %s" % self.mailfrom,
				"To: %s" % self.mailto,
				"Subject: %s" % subject,
				"",
				body
				), "\r\n")
			server = smtplib.SMTP(self.smtphost)
			server.sendmail(self.mailfrom, [ self.mailto ], mailbody)
			server.quit()

	def ExtractFilename(self, filename, dirname):
		dirname = dirname.replace(" ", ".");
		filename = filename.replace(" ", ".");
		match = re.match('^(.*)\.S(\d+)E(\d+)(.*)', dirname, re.IGNORECASE);
		if match is None:
			match = re.match('^(.*)\.S(\d+)E(\d+)(.*)', os.path.splitext(filename)[0], re.IGNORECASE);
			if match is None:
				logging.warning("		Error: Unknown filename pattern (" + filename + ")")
				return None

		result = {
			"showname"	: match.group(1).replace(".", " "), 
			"season"	: match.group(2),
			"episode"	: match.group(3),
			"release"	: match.group(4)
		}
		return  result
	
	def GetEpisodeDetails(self, tvdbid, season, episode):
		url = self.sickbeardURL + "?cmd=episode&tvdbid=" + str(tvdbid) + "&season=" + str(season) + "&episode=" + str(episode)
		self.do_request(url)
		EpisodeDetails = simplejson.loads(self.resp.read())
		return EpisodeDetails['data']
	
	def GetSubsScore(self, orgfilename, subfilename, quality):
		total = 0
		matches = 0
		for cmp in orgfilename.split("."):
			total = total + 2
			if (re.match("^"+cmp+"\.", subfilename, re.IGNORECASE)):
				logging.debug("         match: ^ " + cmp)
				matches = matches + 2
				continue
			if (re.search("\."+cmp+"\.", subfilename, re.IGNORECASE)):
				logging.debug("         match: + " + cmp)
				matches = matches + 2
				continue
			if (re.search("\."+cmp+"$", subfilename, re.IGNORECASE)):
				logging.debug("         match: $ " + cmp)
				matches = matches + 2
				continue
			if (re.search(cmp, subfilename, re.IGNORECASE)):
				logging.debug("         match: ~ " + cmp)
				matches = matches + 1
				continue
			logging.debug("         - " + cmp)
		# Check if the quality matches
		if (re.search(quality.replace(" ", "."), subfilename, re.IGNORECASE)):
			if matches != total:
				matches = total - 1
		elif (re.search(quality.replace(" ", ""), subfilename, re.IGNORECASE)):
			if matches != total:
				matches = total - 1
		logging.debug("		Points: " + str(matches) + " of " + str(total))

		perc = (matches / float(total) * 100)
		return perc
	
	def GetSubsCache(self, tvdbid, season, episode, language, orgfilename, quality, dirname):
		logging.debug("		Checking local cache...")
		self.cur.execute("SELECT filename, downloadlink, provider FROM subs WHERE tvdbid = ? AND season = ? AND episode = ? AND language = ?", [ int(tvdbid), int(season), int(episode), language ])
		rows = self.cur.fetchall()
		subs = []
		for row in rows:
			nr = len(subs)
			subs.append(nr)
			score = self.GetSubsScore(orgfilename, row[0], quality)
			subs[nr] = {
				"filename":	row[0],
				"score":	score,
				"provider":	row[2],
				"url":		row[1]
			}
			logging.info("			" + row[0] + ": "+ str(round(score,1)) + "%")

			nr = len(subs)
			subs.append(nr)
			score = self.GetSubsScore(dirname, row[0], quality)
			subs[nr] = {
				"filename":	row[0],
				"score":	score,
				"provider":	row[2],
				"url":		row[1]
			}
			logging.info("			" + row[0] + " (vs dir): "+ str(round(score,1)) + "%")
		return subs
	
	def GetSubtitleFromCache(self, season, episode, orgfilename, tvdbid, quality, dirname, oshash, filesize):
		global APPNAME
		global VERSION

		self.cur.execute("SELECT showname from shows WHERE tvdbid = ? ", [ int(tvdbid) ])
		data = self.cur.fetchone()
		showname = data[0]

		self.bierdopje.RetrieveSubs(tvdbid, season, episode, self.language)
		self.OpenSubtitles.RetrieveSubs(oshash, filesize, season, episode, self.language, orgfilename, tvdbid, showname)
		cache = self.GetSubsCache(tvdbid, season, episode, self.language, orgfilename, quality, dirname)

		highsubs = {
			"filename": "<none>",
			"url":	    "<none>",
			"provider": "<none>",
			"score": -1 }

		for subs in cache:
			if subs['score'] > highsubs['score']:
				highsubs = subs

		return highsubs


	def ScanToRemoveDir(self, dirname):
		files = os.listdir(dirname)
		for filename in files:
			fullpath = os.path.join(dirname, filename)
			if os.path.isdir(fullpath):
				self.ScanToRemoveDir(fullpath)
			else:
				extension = os.path.splitext(fullpath)[1]
				if extension in self.ignore_extensions:
					continue
				else:
					return False;
		return True;


	def SafeRemoveDir(self, directory):
		logging.debug("		SafeRemove of " + directory)
		if self.ScanToRemoveDir(directory):
			logging.debug("			Removing directory !")
			shutil.rmtree(directory)
		else:
			logging.debug("			There are unknown files in it")


	def ProcessFile(self, fullpath):
		filename = os.path.basename(fullpath)
		filesize = os.path.getsize(fullpath)
		dirname = os.path.basename(os.path.dirname(fullpath))

		logging.debug("		Filename: " + filename)
		logging.debug("		Filesize: " +  str(sizeof_fmt(filesize)))
		logging.debug("		Dirname: " + dirname)

		if filesize < (100*1024*1024):
			logging.warning("			Filesize is too small, skipping")
			return False

		extracted = self.ExtractFilename(filename, dirname)
		if extracted == None:
			return False

		logging.debug("		Showname: " + extracted['showname'])

		# Get TVDB ID
		self.cur.execute("SELECT tvdbid, location, showname FROM shows WHERE showname like ?", [ extracted['showname'] ])
		data = self.cur.fetchone()
		if data == None:
			# Checking if there is originally a year behind it
			self.cur.execute("SELECT tvdbid, location, showname FROM shows WHERE showname like '" + extracted['showname'] + " (%)'")
			data = self.cur.fetchone()
			if data == None:
				# Maybe there is a year in the filename, but not in the database
				tmp_showname = re.sub('\ \d{4}', "", extracted['showname'], 1);
				logging.debug("			Temporary showname: " + tmp_showname);
				self.cur.execute("SELECT tvdbid, location, showname FROM shows WHERE showname like ?", [ tmp_showname ])
				data = self.cur.fetchone()
				if data == None:
					# Maybe it's with hooks in the database
					tmp_showname = re.sub(r' (\d{4})', r' (\1)', extracted['showname']);
					logging.debug("			Temporary showname: " + tmp_showname);
					self.cur.execute("SELECT tvdbid, location, showname FROM shows WHERE showname like ?", [ tmp_showname ])
					data = self.cur.fetchone()
					if data == None:
						logging.warning("		ERROR: Showname not found in the sqlite database")
						return False

		tvdbid = data[0]
		location = data[1]
		extracted['showname'] = data[2]
		episodedetails = self.GetEpisodeDetails(tvdbid, int(extracted['season']), int(extracted['episode']))
		extension = os.path.splitext(fullpath)[1]
		if self.config['rename_movies'] == "1":
			newFilename = location + "/" + self.valifyFilename(extracted['showname'] + " - s" + extracted['season'] + "e" + extracted['episode'] + " - " + episodedetails['name'] + extension);
		else:
			newFilename = fullpath

		if os.path.exists(newFilename) and self.config['rename_movies'] == "1":
			logging.warning("		Warning: New file already exists, adding .1")
			nr = 1
			checkFilename = newFilename + "." + str(nr)
			while os.path.exists(checkFilename):
				nr = nr + 1
				checkFilename = newFilename + "." + str(nr)
			newFilename = checkFilename

		opensubtitles_hash = self.OpenSubtitles.hashFile(fullpath)


		logging.debug("		Episode Name: " + episodedetails['name'])
		logging.debug("		Season: " + extracted['season'])
		logging.debug("		Episode: " + extracted['episode'])
		logging.debug("		Airdate: " + episodedetails['airdate'])
		logging.debug("		TVDB ID: " + str(tvdbid))
		logging.debug("		Location: " + newFilename)
		logging.debug("		Quality: " + episodedetails['quality'])
		logging.debug("		OS hash: " + opensubtitles_hash)

		if not self.dry:
			self.notify("Download", "Completed download of:\nShow: " + extracted['showname'] + "\nSeason: " + extracted['season'] + "\nEpisode: " + extracted['episode'] + "\nAirdate: " + episodedetails['airdate'] + "\nTitle: " + episodedetails['name'])

			qry = "INSERT INTO downloaded (orgfilename, tvdbid, season, episode, newname, subtitles, airdate, quality, dirname, oshash, filesize) VALUES ("
			qry+= ":orgfilename, :tvdbid, :season, :episode, :newname, :subtitles, :airdate, :quality, :dirname, :oshash, :filesize)"
			values = { "orgfilename"	: filename,
				   "tvdbid"		: int(tvdbid),
				   "season"		: int(extracted['season']),
				   "episode"		: int(extracted['episode']),
				   "newname"		: newFilename,
				   "subtitles"		: 0,
				   "airdate"		: episodedetails['airdate'],
				   "quality"		: episodedetails['quality'],
				   "dirname"		: dirname,
				   "oshash"		: opensubtitles_hash,
				   "filesize"		: filesize }
			self.cur.execute(qry, values)
			self.db.commit()
			
			if self.config['rename_movies'] == "1":
				logging.info("		Moving '"+fullpath+"' to '"+newFilename+"'")
				shutil.move(fullpath, newFilename)
				logging.info("		Moved !")
				url = self.sickbeardURL + "?cmd=show.refresh&tvdbid=" + str(tvdbid)
				self.do_request(url)

		if self.config['remove_subdirectory'] == "1":
			self.SafeRemoveDir(os.path.dirname(fullpath))
			

	def ScanDir(self, dirname):
		# logging.debug("Scanning for new shows in " + dirname)
		files = os.listdir(dirname)
		for filename in files:
			fullpath = os.path.join(dirname, filename)
			if os.path.isdir(fullpath):
				self.ScanDir(fullpath)
			else:
				extension = os.path.splitext(fullpath)[1]
				if extension in self.ignore_extensions:
					continue
				elif extension in self.movie_extensions:
					logging.info("Processing: " + fullpath)
					logging.info("	Known movie type, full processing...")
					self.ProcessFile(fullpath)
				else:
					logging.info("Processing: " + fullpath)
					logging.warning("	'"+extension+"' is unknown")
					continue
	
	def CreateDatabaseTables(self):
		self.cur.execute("CREATE TABLE IF NOT EXISTS shows (id INTEGER PRIMARY KEY AUTOINCREMENT, showname TEXT, tvdbid INTEGER, location TEXT)")
		self.cur.execute("CREATE TABLE IF NOT EXISTS downloaded (id INTEGER PRIMARY KEY AUTOINCREMENT, orgfilename TEXT, tvdbid INTEGER, season INTEGER, episode INTEGER, newname TEXT, subtitles NUMERIC, airdate TEXT, quality TEXT, lastcheck TEXT, subsscore REAL, dirname TEXT, oshash text, filesize INTEGER)")
		self.cur.execute("CREATE TABLE IF NOT EXISTS subs (id INTEGER PRIMARY KEY AUTOINCREMENT, tvdbid INTEGER, season INTEGER, episode INTEGER, filename TEXT, downloadlink TEXT, language TEXT, score REAL DEFAULT -1, provider TEXT)");
		self.cur.execute("CREATE TABLE IF NOT EXISTS config (id INTEGER PRIMARY KEY AUTOINCREMENT, param TEXT, value TEXT)")
		self.db.commit()
		# For some weird reason, this query's will make it fail that if you do a drop first and try again to create it
		# Quick solution: close the database and open it again
		self.cur.close()
		self.db.close()
		self.db = sqlite3.connect(self.dbname)
		self.cur = self.db.cursor()
	
	def OpenDatabase(self, db):
		global APPNAME
		global VERSION

		logging.debug("Open database")
		self.dbname = db

		self.db = sqlite3.connect(self.dbname)
		self.cur = self.db.cursor()
		self.cur.execute("SELECT SQLITE_VERSION()")
		data = self.cur.fetchone()
		logging.info("SQLite version: " + data[0])
		self.CreateDatabaseTables()

		# Get Database version
		self.cur.execute("SELECT value FROM config WHERE param = 'database_version'")
		data = self.cur.fetchone()
		if data == None:
			logging.info("Database update required...")
			# Legacy stuff didn't had the provider
			try:
				self.cur.execute('ALTER TABLE subs ADD COLUMN provider TEXT;')
				self.db.commit()
			except:
				pass

			# Let's remove legacy stuff and rebuild the database
			self.PurgeOldSubs()

			# Save current information in the database
			self.cur.execute("SELECT tvdbid, season, episode, filename, downloadlink, language, provider FROM subs")
			dataSubs = self.cur.fetchall()

			self.cur.execute("SELECT orgfilename, tvdbid, season, episode, newname, subtitles, airdate, quality, lastcheck, dirname, subsscore FROM downloaded")
			dataDownloaded = self.cur.fetchall()

			self.db.execute("DROP TABLE downloaded")
			self.db.execute("DROP TABLE shows")
			self.db.execute("DROP TABLE subs");
			self.db.execute("DROP TABLE config")
			self.db.commit()

			self.CreateDatabaseTables()

			for row in dataDownloaded:
				orgfilename = row[0]
				tvdbid = row[1]
				season = row[2]
				episode = row[3]
				newname = row[4]
				subtitles = row[5]
				airdate = row[6]
				quality = row[7]
				lastcheck = row[8]
				dirname = row[9]
				subsscore = row[10]

				qry = "INSERT INTO downloaded (orgfilename, tvdbid, season, episode, "
				qry+= "newname, subtitles, airdate, quality, "
				qry+= "lastcheck, dirname, subsscore) "
				qry+= "VALUES ("
				qry+= ":orgfilename, :tvdbid, :season, :episode, :newname, :subtitles, "
				qry+= ":airdate, :quality, :lastcheck, :dirname, :subsscore)"

				values = { "orgfilename" : orgfilename,
					   "tvdbid" : tvdbid,
					   "season" : season,
					   "episode" : episode,
					   "newname" : newname,
					   "subtitles" : subtitles,
					   "airdate" : airdate,
					   "quality" : quality,
					   "lastcheck" : lastcheck,
					   "dirname" : dirname,
					   "subsscore" : subsscore }
				self.cur.execute(qry, values)
			self.db.commit()

			for row in dataSubs:
				tvdbid = row[0]
				season = row[1]
				episode = row[2]
				filename = row[3]
				downloadlink = row[4]
				language = row[5]
				provider = row[6]
				if len(provider) == 0:
					provider = "bierdopje"

				qry = "INSERT INTO subs (tvdbid, season, episode, filename, downloadlink, language, provider)"
				qry+= "VALUES (: tvdbid, :season, :episode, :filename, :downloadlink, :language, :provider)"
				values = { "tvdbid" : tvdbid, "season" : season, "episode" : episode, "filename" : filename, "downloadlink" : downloadlink, "language" : language, "provider" : provider }
				self.cur.execute(qry, values)
			self.db.commit()
			self.cur.execute("INSERT INTO config (param, value) VALUES ('database_version', '0.1')")
		else:
			self.dbversion = data[0]
			logging.info("Database version: " + self.dbversion)

	def OpenSickbeard(self):
		logging.debug("Opening Sickbeard...")
		url = self.sickbeardURL + "?cmd=shows"
		self.do_request(url)
		Shows = simplejson.loads(self.resp.read())
		database_data = False
		for tvdbid in Shows['data']:
			url = self.sickbeardURL + "?cmd=show&tvdbid=" + str(tvdbid)
			self.do_request(url)
			ShowDetails = simplejson.loads(self.resp.read())
			self.cur.execute("SELECT tvdbid FROM shows WHERE tvdbid = '" + str(tvdbid) + "'")
			data = self.cur.fetchone()
			if data:
				continue

			logging.info("Adding show '" + ShowDetails['data']['show_name'] + "' to the database")
			self.cur.execute("INSERT INTO shows (showname, tvdbid, location) VALUES (?, ?, ?)", [ShowDetails['data']['show_name'], tvdbid, ShowDetails['data']['location']] )
			database_data = True

		if database_data:
			self.db.commit()
	
	def ScanForSubs(self):
		self.cur.execute("SELECT orgfilename, tvdbid, season, episode, newname, quality, airdate, lastcheck, dirname, subsscore, oshash, filesize FROM downloaded WHERE subtitles = 0")
		rows = self.cur.fetchall()
		logging.info("Scanning for subtitles")
		for row in rows:
			orgfilename = row[0]
			tvdbid = row[1]
			season = row[2]
			episode = row[3]
			newname = row[4]
			quality = row[5]
			airdate = row[6]
			lastcheck = row[7]
			dirname = row[8]
			oldsubscore = row[9]
			oshash = row[10]
			filesize = row[11]
			airdate = airdate.split("-")
			airdate = datetime.date(int(airdate[0]), int(airdate[1]), int(airdate[2]))

			delta_airdate = datetime.date.today() - airdate

			logging.info("	Processing: " + os.path.basename(newname))
			logging.debug("		Airdate: " + str(delta_airdate.days) + " days ago")
			logging.debug("		Original filename: " + orgfilename)
			logging.debug("		Original dirname: " + dirname)
			logging.debug("		Subtitles current score: " + str(oldsubscore))
			if lastcheck != None:
				lastcheck = lastcheck.split("-")
				lastcheck = datetime.date(int(lastcheck[0]), int(lastcheck[1]), int(lastcheck[2]))
				delta_checked = datetime.date.today() - lastcheck
				logging.info("		Last check: " + str(lastcheck))
				if int(delta_checked.days) < 1:
					logging.info("		Subtitles already checked today, skipping")
					continue
			else:
				lastcheck = datetime.date.today()

			subtitle = self.GetSubtitleFromCache(season, episode, orgfilename, tvdbid, quality, dirname, oshash, filesize)
			logging.debug("		Highest score subtitle: " + str(subtitle['score']))
			if not self.dry:
				self.cur.execute("UPDATE downloaded SET lastcheck = date() WHERE orgfilename = ?", [orgfilename])
				if subtitle['score'] > oldsubscore:
					logging.info("		Downloading subtitle, old score of the subs was " + str(oldsubscore) + ", new score: " + str(subtitle['score']) + ".")

					if subtitle['provider'] == "bierdopje":
						SubtitleFilename = self.bierdopje.downloadSubs(subtitle['url'], newname, self.language)
					elif subtitle['provider'] == "opensubtitles":
						SubtitleFilename = self.OpenSubtitles.downloadSubs(subtitle['url'], newname, subtitle['filename'], self.language)
					else:
						logging.warning("		Unknown subtitle provider: " + subtitle['provider'])
						self.db.commit()
						continue

					self.cur.execute("UPDATE downloaded SET subsscore = ? WHERE orgfilename = ?", [ subtitle['score'], orgfilename ])
					self.notify("Subtitles", "Found subtitles for:\n" + SubtitleFilename + "\nScore: " + str(subtitle['score']))
					if subtitle['score'] >= self.subs_threshold:
						logging.info("		Passed threshold for subtitles, marking as completed")
						self.cur.execute("UPDATE downloaded set subtitles=1 WHERE orgfilename = ?", [ orgfilename ])
				else:
					if oldsubscore > 0:
						logging.info("		No new subtitle found (old one scored: " + str(oldsubscore) + ".")
					else:
						logging.info("		No subtitle found at all.")

				if int(delta_airdate.days) > self.subs_days:
					if subtitle['score'] < 1:
						logging.info("		Passed the days to check for subs, no subtitles found, manually action required !")
						self.notify("Subtitles", "No subtitle found at all, please find one yourself for: \n" + newname)
					else:
						logging.info("		Passed the days to check for subs, hopefully the one downloaded will do it...")
					self.cur.execute("UPDATE downloaded set subtitles = 1 WHERE orgfilename = ?", [ orgfilename ])
				self.db.commit()

	def OpenNMA(self, apikey):
		self.nma = NMA(apikey)
		if not self.nma.verifykey():
			logging.warning("API key for NMA is not valid")
			self.nma = None
		else:
			logging.debug("API for NMA is valid !")
	
	def PurgeOldSubs(self):
		self.cur.execute("SELECT orgfilename, newname FROM downloaded")
		rows = self.cur.fetchall()
		purged = 0
		logging.info("Purging database, deleting downloaded subtitles entries")
		for row in rows:
			orgfilename = row[0]
			newname = row[1]
			if not os.path.exists(newname):
				logging.info("	Removing " + newname + " (org: " + orgfilename + " ).")
				purged += 1
				if not self.dry:
					self.cur.execute("DELETE FROM downloaded WHERE orgfilename = ? ", [ orgfilename ])
		logging.info("	Purging " + str(purged) + " downloaded records.")
		if purged > 0:
			self.db.commit()

		purged = 0
		self.cur.execute("SELECT tvdbid, season, episode FROM subs")
		rows = self.cur.fetchall()
		logging.info("Purging database, delete subs entries")
		for row in rows:
			tvdbid = row[0]
			season = row[1]
			episode = row[2]
			self.cur.execute("SELECT newname FROM downloaded WHERE tvdbid = ? AND season = ? AND episode = ? AND subtitles = 0", [ tvdbid, season, episode ])
			if self.cur.rowcount < 1:
				purged += 1
				if not self.dry:
					self.cur.execute("DELETE FROM subs WHERE tvdbid = ? AND season = ? and episode = ?", [ tvdbid, season, episode ])

		logging.info("	Purging " + str(purged) + " subs records.")
		if purged > 0:
			self.db.commit()

	def run(self):
		global config

		self.db = None

		logging.info("Start main program...")

		self.OpenDatabase(config['database'])

		if 'nma' in config:
			self.OpenNMA(config['nma'])
		else:
			self.nma = None

		if 'mailto' in config:
			self.mailto = config['mailto']
			self.mailfrom = config['mailfrom']
			self.smtphost = config['smtphost']
		else:
			self.mailto = None

		if config['dry'] == "0":
			self.dry = False
		else:
			self.dry = True
		
		self.sickbeardAddress = config['sbaddr']
		self.sickbeardApi = config['sbapi']
		self.sickbeardURL = "http://" + self.sickbeardAddress + "/api/" + self.sickbeardApi + "/"

		self.ignore_extensions = [ ]
		if 'movie_ext_ignore' in config:
			for ext in config['movie_ext_ignore'].split(","):
				if ext[1:1] == ".":
					self.ignore_extensions.append(ext.strip())
				else:
					self.ignore_extensions.append("."+ext.strip())

		self.movie_extensions = [ ]
		for ext in config['movie_extensions'].split(","):
			if ext[1:1] == ".":
				self.movie_extensions.append(ext.strip())
			else:
				self.movie_extensions.append("."+ext.strip())

		self.language = config['language']
		self.sleep = int(config['sleep'])
		self.maxloops = int(config['loops'])
		self.subs_threshold = int(config['subs_threshold'])
		self.subs_days = int(config['subs_days'])

		self.currentloops = 0

		self.OpenSubtitles = opensubtitles.OpenSubtitles(self.db, logging, "http://api.opensubtitles.org/xml-rpc")
		if self.OpenSubtitles.login():
			logging.debug("Successfully logged in to OpenSubtitles")
		else:
			logging.warning("Error logging in to OpenSubtitles")
		self.bierdopje = bierdopje.bierdopje(config['bierdopje_address'], config['bierdopje_api'], APPNAME + "/" + VERSION, self.db, logging)
		
		self.config = config

		while True:
			self.OpenSickbeard()
			self.ScanDir(config['showdir'])
			self.PurgeOldSubs()
			self.ScanForSubs()
			self.currentloops += 1
			if self.maxloops > 0:
				logging.info("Loop " + str(self.currentloops) + " of " + str(self.maxloops))
				if self.currentloops >= self.maxloops:
					logging.info("Finished")
					sys.exit(0)
			time.sleep(self.sleep)

def Usage():
	global APPNAME
	global VERSION

	print "  Usage: ", sys.argv[0], " -f [configfile]"
	print "Example: ", sys.argv[0], " -f ~/" + APPNAME + ".conf"
	print
	print "The following options are possible:"
	print "	-f, --file [config]	Configuration file"
	print
	print "	-s, --stop		Stop the daemon"
	print "	-r, --restart		Restart the daemon"
	print

def readOpts(opts):
	global config

	for option, value in opts:
		if option in ("-h", "--help"):
			print APPNAME + " v" + VERSION
			print
			Usage()
			sys.exit(0)
		elif option in ("--version"):
			print APPNAME + " v" + VERSION
			print
			sys.exit(0)
		elif option in ("-s", "--stop"):
			config["action"] = "stop"
		elif option in ("-r", "--restart"):
			config["action"] = "restart"
		elif option in ("-f", "--file"):
			config['configfile'] = value

def parseConfig(configfile):
	global config
	
	parser = SafeConfigParser()
	parser.read(configfile)
	for name, value in parser.items(APPNAME):
		config[name] = value

def checkConfig():
	global config

	errorCode = 0


	if not 'showdir' in config:
		print
		print "ERROR: Showdir configuration parameter not found."
		print "This parameter specifies where SABnzbd has downloaded the shows."
		print
		print "Please add the following line to your config file under the section ["+APPNAME+"]:"
		print "SHOWDIR = <directory>"
		print
		sys.exit(3)
	
	if not os.path.isdir(config['showdir']):
		print
		print "ERROR: Showdir ("+config['showdir']+") is not a directory or not readable."
		print
		sys.exit(4)
		
	
	if not os.access(config['showdir'], os.W_OK):
		print
		print "ERROR: Showdir ("+config['showdir']+") needs to be writable"
		print
		sys.exit(5)
	
	
	if 'logfile' in config:
		if os.path.isdir(config['logfile']):
			print
			print "ERROR: Logfile '" + config['logfile'] + "' is a directory, please specify a filename."
			print
			sys.exit(6)
		if not os.path.exists(os.path.dirname(config['logfile'])):
			print
			print "ERROR: The directory specified for the logfile (" + os.path.dirname(config['logfile']) + ") does not exists."
			print
			sys.exit(7)
		if os.path.exists(config['logfile']):
			if not os.access(config['logfile'], os.W_OK):
				print
				print "ERROR: The logfile specified (" + config['logfile'] + ") is not writable."
				print
				sys.exit(8)
		else:
			if not os.access(os.path.dirname(config['logfile']), os.W_OK):
				print
				print "ERROR: The directory of the logfile specified (" + config['logfile'] + ") is not writable."
				print
				sys.exit(8)
	
	if 'database' not in config:
		print
		print "ERROR: No database is specified in the configuration file"
		print "Please add the following line to the config under the section ["+APPNAME+"]:"
		print "DATABASE = <dbfile>"
		print 
		print "In example:"
		print "DATABASE = /var/db/"+APPNAME+".db"
		print
		sys.exit(9)
	
	if os.path.isdir(config['database']):
		print
		print "ERROR: Database ("+config['database']+") is a directory, please specify a file"
		print
		sys.exit(9)
	
	if not os.path.exists(os.path.dirname(config['database'])):
		print
		print "ERROR: The directory specified for the database ("+os.path.dirname(config['database'])+") does not exists."
		print
		sys.exit(10)
	
	if os.path.exists(config['database']):
		if not os.access(config['database'], os.W_OK):
			print
			print "ERROR: The database file ("+config['database']+") is not writable."
			print
			sys.exit(11)
	else:
		if not os.access(os.path.dirname(config['database']), os.W_OK):
			print
			print "ERROR: The directory of the database file ("+config['database']+") is not writable."
			print
			sys.exit(11)
		

	if 'pidfile' not in config:
		print
		print "ERROR: No pidfile is specified in the configuration file"
		print "Please add the following line to the config under the section ["+APPNAME+"]:"
		print "PIDFILE = <pidfile>"
		print 
		print "In example:"
		print "PIDFILE = /var/run/"+APPNAME+".pid"
		print
		sys.exit(12)
	
	if os.path.isdir(config['pidfile']):
		print
		print "ERROR: PID file ("+config['pidfile']+") is a directory, please specify a file"
		print
		sys.exit(13)
	
	if not os.path.exists(os.path.dirname(config['pidfile'])):
		print
		print "ERROR: The directory specified for the pidfile ("+os.path.dirname(config['pidfile'])+") does not exists."
		print
		sys.exit(14)

	if os.path.exists(config['pidfile']):
		if not os.access(config['pidfile'], os.W_OK):
			print
			print "ERROR: The pidfile ("+config['pidfile']+") is not writable."
			print
			sys.exit(15)
	else:
		if not os.access(os.path.dirname(config['pidfile']), os.W_OK):
			print
			print "ERROR: The directory of the pidfile ("+config['pidfile']+") is not writable."
			print
			sys.exit(15)

	if 'sbaddr' not in config:
		print
		print "ERROR: No Sick Beard Address is specified in the configuration file"
		print "Please add the following line to the config under the section ["+APPNAME+"]:"
		print "SBADDR = <address>"
		print 
		print "In example:"
		print "SBADDR = 127.0.0.1:8080"
		print
		sys.exit(16)

	if 'sbapi' not in config:
		print
		print "ERROR: No Sick Beard API is specified in the configuration file"
		print "Please add the following line to the config under the section ["+APPNAME+"]:"
		print "SBAPI = <api>"
		print 
		print "In example:"
		print "SBAPI = 8216d84b49fdda28f5b6181d46d9d061"
		print
		sys.exit(17)

	if 'language' not in config:
		print
		print "ERROR: No language is specified in the configuration file"
		print "Please add the following line to the config under the section ["+APPNAME+"]:"
		print "LANGUAGE = <en,nl>"
		print 
		print "In example:"
		print "LANGUAGE = en"
		print
		sys.exit(18)

	if 'bierdopje_address' not in config:
		print
		print "ERROR: No Bierdopje Address is specified in the configuration file"
		print "Please add the following line to the config under the section ["+APPNAME+"]:"
		print "BIERDOPJE_ADDRESS = api.bierdopje.com"
		print
		sys.exit(19)
	
	if 'bierdopje_api' not in config:
		print
		print "ERROR: No Bierdopje API is specified in the configuration file"
		print "Please add the following line to the config under the section ["+APPNAME+"]:"
		print "BIERDOPJE_API = 495C942B907336C2"
		print
		sys.exit(20)
	
	if 'sleep' not in config:
		config['sleep'] = 600
	
	if not config['sleep'].isdigit():
		print 
		print "ERROR: SLEEP needs to be a number and not " + config['sleep']
		print
		sys.exit(21)
	
	if 'loops' not in config:
		config['loops'] = 0
	
	if not config['loops'].isdigit():
		print 
		print "ERROR: LOOPS needs to be a number and not " + config['loops']
		print
		sys.exit(22)
	
	if 'subs_threshold' not in config:
		config['subs_threshold'] = 85
	
	if not config['subs_threshold'].isdigit():
		print
		print "ERROR: SUBS_THRESHOLD needs to be a number between 1 and 100"
		print
		sys.exit(23)
	
	if int(config['subs_threshold']) < 1 or int(config['subs_threshold']) > 100:
		print
		print "ERROR: SUBS_THRESHOLD needs to be a number between 1 and 100"
		print
		sys.exit(24)

	if 'subs_days' not in config:
		config['subs_days'] = 7

	if not config['subs_days'].isdigit():
		print
		print "ERROR: SUBS_DAYS needs to be a number"
		print
		sys.exit(25)
	
	if 'movie_extensions' not in config:
		print
		print "ERROR: No Movie extensions ares specified in the configuration file"
		print "Please add the following line to the config under the section ["+APPNAME+"]:"
		print "MOVIE_EXTENSIONS = <extensions comma seperated>"
		print 
		print "Example:"
		print "MOVIE_EXTENSIONS = mkv, avi, mpeg, mp4"
		print
		sys.exit(26)
	
	if 'mailto' in config:
		if 'mailfrom' not in config:
			print
			print "ERROR: MAILTO is configured, but it's also required to config"
			print "a MAILFROM then. Please add the following line to the config"
			print "under the section ["+APPNAME+"]:"
			print "MAILFROM = user@domain.tld"
			print
			print "Example:"
			print "MAILFROM = "+APPNAME+"@bitcube.nl"
			print
			sys.exit(27)
		if 'smtphost' not in config:
			print
			print "ERROR: MAILTO is configured, but it's also required to config"
			print "a SMTPHOST then. Please add the following line to the config"
			print "under the section ["+APPNAME+"]:"
			print "SMTPHOST = <smtphost>"
			print
			print "Example:"
			print "SMTPHOST = 127.0.0.1"
			print
			sys.exit(28)

	if 'rename_movies' not in config:
		config['rename_movies'] = 0
	
	if not config['rename_movies'].isdigit():
		print 
		print "ERROR: RENAME_MOVIES needs to be a number and not " + config['rename_movies']
		print
		sys.exit(29)

	
def main(argv):
	global config

	config = { }
	# Open options, and read configfile
	try:
		opts, args = getopt.getopt(argv[1:], "f:hsr", ["file=", "--help", "--version", "--stop", "--restart"])

	except getopt.error, msg:
		print "ERROR: " + str(msg)
		sys.exit(1)

	config['action'] = "start"
	config['dry'] = "1"
	config['remove_subdirectory'] = "0"
	readOpts(opts)

	if not 'configfile' in config:
		if os.path.exists(APPNAME + ".ini"):
			if os.path.isfile(APPNAME+".ini"):
				config['configfile'] = APPNAME+".ini"
		if not 'configfile' in config:
			print "ERROR: No config file is supplied !"
			print
			Usage()
			sys.exit(2)

	if config['configfile']:
		parseConfig(config['configfile'])

	# Check if required parameters are set
	checkConfig()

	# Open logging
	if 'logfile' in config:
		logging.basicConfig(filename=config['logfile'], level=logging.DEBUG, format='%(asctime)s: %(message)s', datefmt='%b %d %H:%M:%S')
	else:
		logging.basicConfig(level=logging.DEBUG, format='%(asctime)s: %(message)s', datefmt='%b %d %H:%M:%S')
	
	daemon = subliminal(config['pidfile'])
	if config['action'] == "stop":
		daemon.stop()
	elif config['action'] == "restart":
		daemon.restart()
	else:
		daemon.start()
	
if __name__ == "__main__":
        sys.exit(main(sys.argv))
