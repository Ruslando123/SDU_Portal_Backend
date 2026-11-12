package org.example.sdu.verification

import org.junit.jupiter.api.*
import org.junit.jupiter.api.Assertions.*
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder
import java.net.CookieManager
import java.net.CookiePolicy
import java.net.URI
import java.net.URLEncoder
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.nio.charset.StandardCharsets
import java.time.Duration
import java.util.UUID

@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class AuthenticationImplementationTest {
    private lateinit var app: Phase5Harness
    private lateinit var f: Fixture
    private lateinit var secret: String
    @BeforeAll fun start() { app=Phase5Harness("--server.servlet.session.cookie.secure=false") }
    @AfterAll fun stop() { app.close() }
    @BeforeEach fun fixture() {
        f=Fixture().populate(app.db);secret=UUID.randomUUID().toString()
        app.db.connection().use { it.execute("UPDATE identity.test_accounts SET password_hash=? WHERE principal_id=?","{bcrypt}"+BCryptPasswordEncoder(4).encode(secret),f.principal) }
    }
    @AfterEach fun invariants() { Phase5InvariantChecks.verify(app.db) }

    @Test fun `student login rotates persisted session uses HttpOnly SameSite and erases verifier`() {
        val browser=Browser();browser.csrf();val old=browser.sessionCookie()
        val login=browser.login();assertEquals(200,login.statusCode(),login.body())
        assertNotEquals(old,browser.sessionCookie())
        val cookie=login.headers().allValues("Set-Cookie").joinToString()
        assertTrue(cookie.contains("HttpOnly"));assertTrue(cookie.contains("SameSite=Lax"))
        val me=browser.get("/api/v1/me");assertEquals(200,me.statusCode());assertTrue(me.body().contains(f.student.toString()))
        assertEquals(1L,app.db.count("SELECT count(*) FROM identity.spring_session WHERE principal_name=?",f.principal.toString()))
        val serialized=app.db.value("SELECT attribute_bytes FROM identity.spring_session_attributes a JOIN identity.spring_session s ON s.primary_id=a.session_primary_id WHERE s.principal_name=? AND a.attribute_name='SPRING_SECURITY_CONTEXT'",f.principal.toString()) as ByteArray
        assertFalse(String(serialized,StandardCharsets.ISO_8859_1).contains("{bcrypt}"))
        assertFalse(String(serialized,StandardCharsets.ISO_8859_1).contains(secret))
    }
    @Test fun `invalid credentials are generic and cannot authenticate a session`() {
        val browser=Browser();browser.csrf();assertEquals(401,browser.login("invalid").statusCode())
        assertEquals(401,browser.get("/api/v1/me").statusCode())
    }
    @Test fun `CSRF protects login enrollment withdrawal and logout`() {
        val browser=Browser()
        assertEquals(403,browser.send("POST","/api/v1/auth/login","username=x&password=x",csrf=false,form=true).statusCode())
        browser.csrf();assertEquals(200,browser.login().statusCode());browser.csrf()
        val path="/api/v1/sections/${f.section}/enrollments"
        assertEquals(403,browser.send("POST",path,csrf=false).statusCode())
        assertEquals(403,browser.send("POST","/api/v1/auth/logout",csrf=false).statusCode())
        val enrollment=browser.send("POST",path);assertEquals(201,enrollment.statusCode(),enrollment.body())
        val id=app.mapper.readTree(enrollment.body()).get("enrollmentId").asString()
        assertEquals(403,browser.send("DELETE","/api/v1/enrollments/$id",csrf=false).statusCode())
        assertEquals(200,browser.send("DELETE","/api/v1/enrollments/$id").statusCode())
    }
    @Test fun `logout invalidates server session and old cookie cannot be reused`() {
        val browser=loggedIn();val cookie=browser.sessionCookie()
        assertEquals(200,browser.send("POST","/api/v1/auth/logout").statusCode())
        assertEquals(401,browser.get("/api/v1/me").statusCode())
        val request=HttpRequest.newBuilder(URI.create(app.base+"/api/v1/me")).header("Cookie","SESSION=$cookie").GET().build()
        assertEquals(401,HttpClient.newHttpClient().send(request,HttpResponse.BodyHandlers.ofString()).statusCode())
    }
    @Test fun `disabled account and incremented auth version revoke existing sessions before replay`() {
        var browser=loggedIn()
        app.db.connection().use { it.execute("UPDATE identity.test_accounts SET enabled=false WHERE principal_id=?",f.principal) }
        assertEquals(401,browser.get("/api/v1/me").statusCode())
        val disabled=Browser();disabled.csrf();assertEquals(401,disabled.login().statusCode())
        app.db.connection().use { it.execute("UPDATE identity.test_accounts SET enabled=true WHERE principal_id=?",f.principal) }
        browser=loggedIn();val key=UUID.randomUUID().toString()
        assertEquals(201,browser.send("POST","/api/v1/sections/${f.section}/enrollments",key=key).statusCode())
        app.db.connection().use { it.execute("UPDATE identity.test_accounts SET auth_version=auth_version+1 WHERE principal_id=?",f.principal) }
        assertEquals(401,browser.send("POST","/api/v1/sections/${f.section}/enrollments",key=key).statusCode())
    }
    @Test fun `admin login succeeds but student commands and own schedule are forbidden`() {
        app.db.connection().use { c -> c.execute("DELETE FROM identity.test_accounts WHERE principal_id=?",f.principal)
            c.execute("INSERT INTO identity.test_accounts VALUES (?,NULL,?,?,'ADMIN',true,1,clock_timestamp())",f.principal,"test-${f.principal}","{bcrypt}"+BCryptPasswordEncoder(4).encode(secret)) }
        val browser=loggedIn();assertTrue(browser.get("/api/v1/me").body().contains("ADMIN"))
        assertEquals(403,browser.send("POST","/api/v1/sections/${f.section}/enrollments").statusCode())
        assertEquals(403,browser.get("/api/v1/me/schedule?termId=${f.term}").statusCode())
    }
    @Test fun `new and legacy API aliases share intent and reads return only the authenticated student`() {
        val browser=loggedIn();val other=f.anotherStudent(app.db)
        val key=UUID.randomUUID().toString()
        val result=browser.send("POST","/api/v1/sections/${f.section}/enrollments",key=key)
        assertEquals(201,result.statusCode(),result.body())
        val replay=browser.send("POST","/api/v1/me/enrollments","{\"sectionId\":\"${f.section}\",\"studentId\":\"${other.student}\",\"role\":\"ADMIN\"}",key=key)
        assertEquals(201,replay.statusCode(),replay.body());assertEquals(result.body(),replay.body())
        assertEquals("true",replay.headers().firstValue("Idempotency-Replayed").orElseThrow())
        val own=browser.get("/api/v1/me/enrollments?termId=${f.term}");assertEquals(200,own.statusCode());assertFalse(own.body().contains(other.student.toString()))
        val schedule=browser.get("/api/v1/me/schedule?termId=${f.term}");assertEquals(200,schedule.statusCode(),schedule.body());assertTrue(schedule.body().contains("UTC"));assertTrue(schedule.body().contains("LECTURE"))
        assertEquals(200,browser.get("/api/v1/courses/${f.course}/sections?termId=${f.term}").statusCode())
        assertEquals(400,browser.get("/api/v1/me/enrollments?termId=${f.term}&limit=101").statusCode())
    }
    @Test fun `missing malformed idempotency keys and malformed resource identifiers fail safely`() {
        val browser=loggedIn()
        assertEquals(400,browser.send("POST","/api/v1/sections/${f.section}/enrollments",key=null).statusCode())
        assertEquals(400,browser.send("POST","/api/v1/sections/${f.section}/enrollments",key="invalid").statusCode())
        assertEquals(400,browser.send("POST","/api/v1/sections/invalid/enrollments").statusCode())
        assertEquals(400,browser.get("/api/v1/me/enrollments").statusCode())
    }
    @Test fun `foreign enrollment cannot be withdrawn over HTTP even with forged identity headers`() {
        val other=f.anotherStudent(app.db);val otherId=app.id(app.enroll(other));val browser=loggedIn()
        val result=browser.send("DELETE","/api/v1/enrollments/$otherId")
        assertEquals(404,result.statusCode());assertFalse(result.body().contains(other.student.toString()));assertFalse(result.body().contains(otherId.toString()))
    }

    @Test fun `a second backend resolves the PostgreSQL session and original committed replay`() {
        val browser=loggedIn();val key=UUID.randomUUID().toString()
        val original=browser.send("POST","/api/v1/sections/${f.section}/enrollments",key=key)
        assertEquals(201,original.statusCode())
        app.replica("--server.servlet.session.cookie.secure=false").use { replica ->
            val port=requireNotNull((replica as org.springframework.boot.web.server.context.WebServerApplicationContext).webServer).port
            browser.base="http://127.0.0.1:$port"
            assertEquals(200,browser.get("/api/v1/me").statusCode())
            val replay=browser.send("POST","/api/v1/sections/${f.section}/enrollments",key=key)
            assertEquals(original.body(),replay.body());assertEquals("true",replay.headers().firstValue("Idempotency-Replayed").orElseThrow())
        }
    }

    @Test fun `catalog replica fails closed when pointed at primary and seat reads and writes remain available`() {
        val browser = loggedIn()
        app.replica("--server.servlet.session.cookie.secure=false",
            "--SDU_CATALOG_REPLICA_URL=${app.db.url}", "--SDU_CATALOG_REPLICA_USER=portal_runtime",
            "--SDU_CATALOG_REPLICA_PASSWORD=${TestPostgres.runtimePassword}").use { replica ->
            val port = requireNotNull((replica as org.springframework.boot.web.server.context.WebServerApplicationContext).webServer).port
            browser.base = "http://127.0.0.1:$port"
            assertEquals(503, browser.get("/api/v1/courses?termId=${f.term}&available=false").statusCode())
            assertEquals(200, browser.get("/api/v1/courses?termId=${f.term}&available=true").statusCode())
            assertEquals(201, browser.send("POST", "/api/v1/sections/${f.section}/enrollments").statusCode())
        }
    }

    private fun loggedIn()=Browser().also { it.csrf();assertEquals(200,it.login().statusCode());it.csrf() }
    private inner class Browser {
        var base=app.base
        val cookies=CookieManager(null,CookiePolicy.ACCEPT_ALL)
        val client=HttpClient.newBuilder().cookieHandler(cookies).connectTimeout(Duration.ofSeconds(2)).build()
        private var token=""
        private var header="X-CSRF-TOKEN"
        fun sessionCookie():String=cookies.cookieStore.cookies.first { it.name=="SESSION" }.value
        fun get(path:String)=send("GET",path,csrf=false)
        fun csrf() { val result=get("/api/v1/auth/csrf");assertEquals(200,result.statusCode(),result.body());val json=app.mapper.readTree(result.body());token=json.get("token").asString();header=json.get("headerName").asString() }
        fun login(password:String=secret)=send("POST","/api/v1/auth/login","username=test-${f.principal}&password=${URLEncoder.encode(password,StandardCharsets.UTF_8)}",form=true)
        fun send(method:String,path:String,body:String="",csrf:Boolean=true,form:Boolean=false,key:String?=UUID.randomUUID().toString()):HttpResponse<String> {
            val request=HttpRequest.newBuilder(URI.create(base+path)).timeout(Duration.ofSeconds(12))
                .header("Content-Type",if(form) "application/x-www-form-urlencoded" else "application/json")
                .header("X-Student-ID",UUID.randomUUID().toString()).header("X-Role","ADMIN")
            if(csrf) request.header(header,token)
            if(key!=null) request.header("Idempotency-Key",key)
            return client.send(request.method(method,HttpRequest.BodyPublishers.ofString(body)).build(),HttpResponse.BodyHandlers.ofString())
        }
    }
}
