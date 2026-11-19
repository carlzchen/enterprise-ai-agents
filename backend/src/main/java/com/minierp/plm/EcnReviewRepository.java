package com.minierp.plm;

import org.springframework.data.jpa.repository.JpaRepository;
import java.util.List;
import java.util.Optional;

public interface EcnReviewRepository extends JpaRepository<EcnReview, Long> {
    List<EcnReview> findByEcnId(Long ecnId);
    Optional<EcnReview> findByEcnIdAndReviewRole(Long ecnId, String role);
}
